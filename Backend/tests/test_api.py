import hashlib
import json
import time
from datetime import timedelta
from pathlib import Path

import jwt
import psycopg
import pytest
from google.genai import errors

from app.demo_data import DEMO_DOCUMENTS
from app.sync import SIGNATURE_HEADER, TIMESTAMP_HEADER, signed_headers
from conftest import EXTRACTION, N8N_URL, SECRET, make_settings, sign_in

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
PNG = b"\x89PNG\r\n\x1a\n" + b"card-bytes"
BUSY = errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})


def upload(client, auth, data=PNG, name="card.png"):
    return client.post("/api/documents", files={"file": (name, data, "application/octet-stream")}, headers=auth)


def uploaded(client, auth, **kwargs):
    response = upload(client, auth, **kwargs)
    assert response.status_code == 201, response.text
    return response.json()


def callback(client, body, secret=SECRET, now=None):
    raw = json.dumps(body).encode()
    return client.post("/api/sync/callback", content=raw, headers=signed_headers(secret, raw, now=now))


# Accounts


def test_register_login_and_me(client):
    auth = sign_in(client, "Sara@Example.com")
    me = client.get("/api/me", headers=auth).json()
    assert me["email"] == "sara@example.com" and me["is_demo"] is False
    login = client.post("/api/auth/login", json={"email": "sara@example.com", "password": "correct-horse-battery"})
    assert login.status_code == 200 and login.json()["user"]["id"] == me["id"]


def test_wrong_password_and_duplicate_email(client):
    sign_in(client)
    assert client.post("/api/auth/login", json={"email": "sara@example.com", "password": "wrong-one"}).json() == {"detail": "Email or password is wrong."}
    assert client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "wrong-one"}).status_code == 401
    assert client.post("/api/auth/register", json={"email": "sara@example.com", "password": "another-pass"}).status_code == 409


def test_short_password_is_refused_without_echoing_it(client):
    response = client.post("/api/auth/register", json={"email": "a@example.com", "password": "pw4tiny"})
    assert response.status_code == 422
    assert "pw4tiny" not in response.text


def test_requests_need_a_valid_token(client):
    assert client.get("/api/documents").status_code == 401
    assert client.get("/api/documents", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def old_token(client, auth, seconds_ago=60):
    """The same user's token, issued a little while back."""
    user_id = client.get("/api/me", headers=auth).json()["id"]
    now = int(time.time())
    token = jwt.encode({"sub": user_id, "iat": now - seconds_ago, "exp": now + 3600}, make_settings("")["jwt_secret"], algorithm="HS256")
    return {"Authorization": "Bearer " + token}


def change_password(client, auth, current="correct-horse-battery", new="a-brand-new-secret"):
    return client.post("/api/me/password", json={"current_password": current, "new_password": new}, headers=auth)


def test_change_password_ends_older_sessions_and_keeps_this_one(client, auth):
    earlier = old_token(client, auth)
    assert client.get("/api/me", headers=earlier).json()["has_password"] is True
    response = change_password(client, auth)
    assert response.status_code == 200, response.text
    fresh = {"Authorization": "Bearer " + response.json()["token"]}
    assert client.get("/api/me", headers=fresh).status_code == 200
    assert client.get("/api/me", headers=earlier).status_code == 401
    login = lambda password: client.post("/api/auth/login", json={"email": "sara@example.com", "password": password}).status_code
    assert login("correct-horse-battery") == 401
    assert login("a-brand-new-secret") == 200


def test_change_password_needs_the_current_one(client, auth):
    response = change_password(client, auth, current="not-my-password")
    assert response.status_code == 400 and response.json() == {"detail": "Your current password is not correct."}
    assert client.post("/api/me/password", json={"new_password": "a-brand-new-secret"}, headers=auth).status_code == 400
    assert client.post("/api/auth/login", json={"email": "sara@example.com", "password": "correct-horse-battery"}).status_code == 200


def test_new_password_must_be_long_enough_and_is_not_echoed(client, auth):
    response = change_password(client, auth, new="tiny7ch")
    assert response.status_code == 422 and "tiny7ch" not in response.text


def test_guessing_the_current_password_is_rate_limited(client, auth):
    codes = [change_password(client, auth, current=f"guess-{n}").status_code for n in range(11)]
    assert codes[:10] == [400] * 10 and codes[10] == 429
    # The limit is per account, so another person isn't affected.
    other = sign_in(client, "omar@example.com")
    assert change_password(client, other).status_code == 200


def test_demo_accounts_cannot_set_a_password(client):
    session = client.post("/api/auth/demo").json()
    assert session["user"]["has_password"] is False
    auth = {"Authorization": "Bearer " + session["token"]}
    response = client.post("/api/me/password", json={"new_password": "a-brand-new-secret"}, headers=auth)
    assert response.status_code == 403
    assert client.post("/api/auth/login", json={"email": session["user"]["email"], "password": "a-brand-new-secret"}).status_code == 401


def test_demo_opens_a_private_account_with_the_samples(client, gemini):
    first = client.post("/api/auth/demo").json()
    second = client.post("/api/auth/demo").json()
    assert first["user"]["is_demo"] and first["user"]["id"] != second["user"]["id"]
    auth = {"Authorization": "Bearer " + first["token"]}
    documents = client.get("/api/documents", headers=auth).json()["documents"]
    assert sorted(d["filename"] for d in documents) == ["business-card.png", "invoice.pdf", "lead-form.pdf"]
    assert {d["status"] for d in documents} == {"needs_review"}
    assert gemini.calls == 0
    lead = next(d for d in documents if d["filename"] == "lead-form.pdf")
    assert lead["title"] == "Lina Haddad"
    fields = client.get(f"/api/documents/{lead['id']}", headers=auth).json()["fields"]
    assert fields["contact.phone"]["issues"] == ["Add the country code, for example +1 for the US."]


def test_demo_accounts_cannot_sign_in_with_a_password(client, store):
    user = client.post("/api/auth/demo").json()["user"]
    assert client.post("/api/auth/login", json={"email": user["email"], "password": "anything"}).status_code == 401


def test_old_demo_accounts_are_cleaned_up(client, database_url):
    old = client.post("/api/auth/demo").json()["user"]["id"]
    with psycopg.connect(database_url, autocommit=True) as db:
        db.execute("UPDATE users SET created_at = now() - interval '3 days' WHERE id = %s", (old,))
    client.post("/api/auth/demo")
    with psycopg.connect(database_url) as db:
        assert db.execute("SELECT count(*) FROM users WHERE id = %s", (old,)).fetchone()[0] == 0


def test_demo_data_matches_the_sample_files():
    for document in DEMO_DOCUMENTS:
        data = (SAMPLES / document["filename"]).read_bytes()
        assert document["size"] == len(data)
        assert document["sha256"] == hashlib.sha256(data).hexdigest()


def test_uploading_a_demo_sample_finds_the_prefilled_copy(client, gemini):
    auth = {"Authorization": "Bearer " + client.post("/api/auth/demo").json()["token"]}
    response = upload(client, auth, data=(SAMPLES / "invoice.pdf").read_bytes(), name="invoice.pdf")
    assert response.status_code == 200 and response.json()["duplicate"] is True
    assert gemini.calls == 0


# Uploads


def test_upload_reads_the_document(client, auth):
    document = uploaded(client, auth)
    assert document["status"] == "needs_review" and document["doc_type"] == "business_card"
    assert document["mime"] == "image/png" and document["title"] == "Omar Siddiqui" and document["duplicate"] is False
    assert document["fields"]["contact.email"]["value"] == "omar@kestrel.example"
    assert document["fields"]["invoice.total"]["value"] is None
    assert document["can_sync"] is True and document["missing"] == []
    assert document["sync_events"] == []


@pytest.mark.parametrize(
    "data, name, mime",
    [(b"%PDF-1.7 ...", "x.pdf", "application/pdf"), (b"\xff\xd8\xff\xe0jpeg", "x.jpg", "image/jpeg"), ("Name: Lina\nEmail: lina@x.example".encode(), "lead.txt", "text/plain")],
)
def test_allowed_types_are_detected_from_the_bytes(client, auth, data, name, mime):
    assert uploaded(client, auth, data=data, name=name)["mime"] == mime


@pytest.mark.parametrize("data, name", [(b"MZ\x90\x00exe", "card.png"), (b"<svg></svg>", "x.svg"), (b"plain text", "notes.md"), (b"\xff\xfe\x00bad", "x.txt")])
def test_other_types_are_refused(client, auth, data, name, gemini):
    response = upload(client, auth, data=data, name=name)
    assert response.status_code == 415 and response.json() == {"detail": "Upload a PDF, PNG, JPG or TXT file."}
    assert gemini.calls == 0


def test_too_big_and_empty(client, auth):
    assert upload(client, auth, data=b"%PDF-" + b"0" * (4 * 1024 * 1024)).status_code == 413
    assert upload(client, auth, data=b"").status_code == 400


def test_same_file_twice_returns_the_first_copy(client, auth, gemini):
    first = uploaded(client, auth)
    again = upload(client, auth)
    assert again.status_code == 200 and again.json()["id"] == first["id"] and again.json()["duplicate"] is True
    assert gemini.calls == 1


def test_another_user_can_upload_the_same_file(client, auth):
    uploaded(client, auth)
    assert upload(client, sign_in(client, "omar@example.com")).status_code == 201


def test_ai_busy_returns_503_and_saves_nothing(client, auth, gemini):
    gemini.replies = [BUSY]
    response = upload(client, auth)
    assert response.status_code == 503
    assert response.json()["detail"] == "The AI service is busy right now. Please try again in a little while."
    assert client.get("/api/documents", headers=auth).json()["documents"] == []


def test_unreadable_document_is_kept_as_failed(client, auth, gemini):
    gemini.replies = [dict(EXTRACTION, is_business_document=False)]
    document = uploaded(client, auth)
    assert document["status"] == "failed" and "doesn't look like an invoice" in document["error"]
    assert document["can_sync"] is False
    assert client.post(f"/api/documents/{document['id']}/sync", headers=auth).status_code == 422


def test_failed_document_can_be_filled_in_by_hand_and_sent(client, auth, gemini):
    gemini.replies = [dict(EXTRACTION, fields=[])]
    document = uploaded(client, auth)
    edited = client.patch(f"/api/documents/{document['id']}/fields", json={"fields": {"contact.email": "omar@kestrel.example"}}, headers=auth).json()
    assert edited["status"] == "needs_review" and edited["error"] is None and edited["can_sync"] is True
    assert client.post(f"/api/documents/{document['id']}/sync", headers=auth).json()["status"] == "synced"


def test_per_user_upload_limit(build, store):
    client = build(uploads_per_user_day=2)
    auth = sign_in(client)
    for index in range(2):
        uploaded(client, auth, data=PNG + bytes([index]))
    response = upload(client, auth, data=PNG + b"x")
    assert response.status_code == 429 and "today's upload limit" in response.json()["detail"]
    assert int(response.headers["Retry-After"]) > 0


def test_daily_limit_for_everyone(build):
    client = build(uploads_per_day=1)
    uploaded(client, sign_in(client, "a@example.com"))
    response = upload(client, sign_in(client, "b@example.com"), data=PNG + b"2")
    assert response.status_code == 429 and "Uploads are full" in response.json()["detail"]


def test_filenames_are_cleaned(client, auth):
    from app.main import clean_filename

    assert uploaded(client, auth, name="../../etc/passwd.png")["filename"] == "passwd.png"
    assert clean_filename("bell\x07\x1b.png") == "bell.png"
    assert clean_filename("") == "document"


# Reading documents


def test_list_is_newest_first_without_fields(client, auth):
    first = uploaded(client, auth, data=PNG + b"1")
    second = uploaded(client, auth, data=PNG + b"2")
    documents = client.get("/api/documents", headers=auth).json()["documents"]
    assert [d["id"] for d in documents] == [second["id"], first["id"]]
    assert "fields" not in documents[0] and "sync_events" not in documents[0]


def test_documents_are_private(client, auth):
    document = uploaded(client, auth)
    other = sign_in(client, "omar@example.com")
    for method, path in (("get", ""), ("patch", "/fields"), ("post", "/sync"), ("delete", "")):
        kwargs = {"json": {"fields": {}}} if method == "patch" else {}
        assert getattr(client, method)(f"/api/documents/{document['id']}{path}", headers=other, **kwargs).status_code == 404
    assert client.get("/api/documents/not-a-uuid", headers=auth).status_code == 404


def test_delete(client, auth):
    document = uploaded(client, auth)
    assert client.delete(f"/api/documents/{document['id']}", headers=auth).status_code == 204
    assert client.get(f"/api/documents/{document['id']}", headers=auth).status_code == 404
    assert client.delete(f"/api/documents/{document['id']}", headers=auth).status_code == 404


# Editing


def test_edits_are_checked_and_marked(client, auth):
    document = uploaded(client, auth)
    edited = client.patch(
        f"/api/documents/{document['id']}/fields",
        json={"fields": {"contact.email": "not an email", "company.name": "Kestrel Logistics Ltd"}},
        headers=auth,
    ).json()
    assert edited["fields"]["contact.email"]["issues"] == ["This doesn't look like a valid email address."]
    assert edited["fields"]["company.name"] == {"value": "Kestrel Logistics Ltd", "confidence": 1.0, "source": "Edited by you", "issues": [], "edited": True}
    assert edited["missing"] == ["contact.email"] and edited["can_sync"] is False


def test_line_items_can_be_edited(client, auth):
    document = uploaded(client, auth)
    items = [{"description": "Desk", "quantity": "2", "unit_price": "480", "amount": "960"}]
    edited = client.patch(f"/api/documents/{document['id']}/fields", json={"fields": {"invoice.line_items": items, "invoice.total": "$960"}}, headers=auth).json()
    assert edited["fields"]["invoice.line_items"]["value"] == [{"description": "Desk", "quantity": "2", "unit_price": "480.00", "amount": "960.00"}]
    assert edited["fields"]["invoice.total"]["issues"] == []


@pytest.mark.parametrize(
    "fields",
    [{"contact.fax": "1"}, {"contact.email": 5}, {"notes": "x" * 2001}, {"invoice.line_items": "Desk"}, {"invoice.line_items": [{"description": {"a": 1}}]}, {"notes": "nul\x00"}],
)
def test_bad_edits_are_refused(client, auth, fields):
    document = uploaded(client, auth)
    assert client.patch(f"/api/documents/{document['id']}/fields", json={"fields": fields}, headers=auth).status_code == 400


def test_editing_a_synced_document_lets_it_be_sent_again(client, auth, services):
    document = uploaded(client, auth)
    synced = client.post(f"/api/documents/{document['id']}/sync", headers=auth).json()
    assert client.post(f"/api/documents/{document['id']}/sync", headers=auth).status_code == 409
    edited = client.patch(f"/api/documents/{document['id']}/fields", json={"fields": {"contact.job_title": "COO"}}, headers=auth).json()
    assert edited["status"] == "needs_review" and edited["can_sync"] is True
    again = client.post(f"/api/documents/{document['id']}/sync", headers=auth).json()
    # The second send updates the records the first one made.
    assert again["crm"]["company_id"] == synced["crm"]["company_id"]
    assert ("PATCH", f"/crm/v3/objects/companies/{synced['crm']['company_id']}") in services.hubspot_calls()


# Sending straight to HubSpot


def test_direct_sync_sends_the_record_and_saves_the_ids(client, auth, services):
    document = uploaded(client, auth)
    result = client.post(f"/api/documents/{document['id']}/sync", headers=auth).json()
    assert result["status"] == "synced" and result["can_sync"] is False
    assert result["crm"]["contact_id"] == "900"
    assert result["crm"]["contact_url"] == "https://app.hubspot.com/contacts/4242/record/0-1/900"
    upsert = next(r for r in services.requests if r.path.endswith("/batch/upsert"))
    assert upsert.body["inputs"][0]["properties"] == {"email": "omar@kestrel.example", "firstname": "Omar", "lastname": "Siddiqui"}
    assert upsert.headers["authorization"] == "Bearer pat-test"
    note = next(r for r in services.requests if r.path == "/crm/v3/objects/notes")
    assert note.body["properties"]["hs_note_body"] == "Imported by Inkfold from card.png."
    assert [(e["channel"], e["status"]) for e in result["sync_events"]] == [("direct", "synced")]
    assert result["sync_events"][0]["detail"].startswith("Saved in HubSpot: contact 900")


def test_direct_sync_failure_is_shown_and_can_be_retried(client, auth, services):
    document = uploaded(client, auth)
    services.hubspot_status = 401
    failed = client.post(f"/api/documents/{document['id']}/sync", headers=auth).json()
    assert failed["status"] == "sync_failed" and failed["error"] == "HubSpot rejected the access token. Check HUBSPOT_TOKEN."
    assert failed["can_sync"] is True
    services.hubspot_status = None
    assert client.post(f"/api/documents/{document['id']}/sync", headers=auth).json()["status"] == "synced"


def test_sync_needs_a_valid_email(client, auth, gemini):
    gemini.replies = [dict(EXTRACTION, fields=[f for f in EXTRACTION["fields"] if f["key"] != "contact.email"])]
    document = uploaded(client, auth)
    response = client.post(f"/api/documents/{document['id']}/sync", headers=auth)
    assert response.status_code == 422
    assert response.json() == {"detail": "Add a valid email before sending to HubSpot.", "missing": ["contact.email"]}


def test_sync_without_hubspot_or_n8n(build):
    client = build(hubspot_token="")
    auth = sign_in(client)
    document = uploaded(client, auth)
    response = client.post(f"/api/documents/{document['id']}/sync", headers=auth)
    assert response.status_code == 503 and response.json()["detail"] == "HubSpot isn't connected yet."
    assert client.get(f"/api/documents/{document['id']}", headers=auth).json()["status"] == "needs_review"


# Sending through n8n


@pytest.fixture
def n8n(build):
    return build(n8n_webhook_url=N8N_URL, n8n_shared_secret=SECRET)


def test_n8n_gets_a_signed_payload(n8n, services):
    auth = sign_in(n8n)
    document = uploaded(n8n, auth)
    result = n8n.post(f"/api/documents/{document['id']}/sync", headers=auth).json()
    assert result["status"] == "syncing" and result["can_sync"] is False
    assert services.hubspot_calls() == []
    sent = services.n8n_calls()[0]
    assert sent.body["document_id"] == document["id"]
    assert sent.body["callback_url"] == "http://api.test/api/sync/callback"
    assert sent.body["record"]["contact"]["email"] == "omar@kestrel.example"
    assert sent.body["existing"] == {}
    from app.sync import verify_signature

    assert verify_signature(SECRET, sent.headers[TIMESTAMP_HEADER.lower()], sent.headers[SIGNATURE_HEADER.lower()], sent.content)


def test_second_send_while_waiting_is_refused(n8n, services):
    auth = sign_in(n8n)
    document = uploaded(n8n, auth)
    n8n.post(f"/api/documents/{document['id']}/sync", headers=auth)
    assert n8n.post(f"/api/documents/{document['id']}/sync", headers=auth).status_code == 409
    assert n8n.patch(f"/api/documents/{document['id']}/fields", json={"fields": {"notes": "x"}}, headers=auth).status_code == 409
    assert len(services.n8n_calls()) == 1


def test_a_send_that_never_heard_back_can_be_retried(n8n, database_url, services):
    auth = sign_in(n8n)
    document = uploaded(n8n, auth)
    n8n.post(f"/api/documents/{document['id']}/sync", headers=auth)
    with psycopg.connect(database_url, autocommit=True) as db:
        db.execute("UPDATE documents SET updated_at = now() - interval '11 minutes' WHERE id = %s", (document["id"],))
    assert n8n.get(f"/api/documents/{document['id']}", headers=auth).json()["can_sync"] is True
    assert n8n.post(f"/api/documents/{document['id']}/sync", headers=auth).status_code == 200
    assert len(services.n8n_calls()) == 2


def test_n8n_unreachable(n8n, services):
    auth = sign_in(n8n)
    document = uploaded(n8n, auth)
    services.n8n_status = 404
    result = n8n.post(f"/api/documents/{document['id']}/sync", headers=auth).json()
    assert result["status"] == "sync_failed" and "status 404" in result["error"]
    assert [(e["channel"], e["status"]) for e in result["sync_events"]] == [("n8n", "sent"), ("n8n", "failed")]


def test_signed_callback_marks_the_document_synced(n8n):
    auth = sign_in(n8n)
    document = uploaded(n8n, auth)
    n8n.post(f"/api/documents/{document['id']}/sync", headers=auth)
    crm = {"contact_id": "7", "company_id": "8", "deal_id": None, "note_id": "9", "contact_url": "https://app.hubspot.com/contacts/1/record/0-1/7", "deal_url": None}
    assert callback(n8n, {"document_id": document["id"], "status": "synced", "crm": {**crm, "extra": "ignored"}}).json() == {"ok": True}
    result = n8n.get(f"/api/documents/{document['id']}", headers=auth).json()
    assert result["status"] == "synced" and result["crm"] == crm
    assert [(e["channel"], e["status"]) for e in result["sync_events"]] == [("n8n", "sent"), ("n8n", "synced")]


def test_replayed_callback_changes_nothing(n8n):
    auth = sign_in(n8n)
    document = uploaded(n8n, auth)
    n8n.post(f"/api/documents/{document['id']}/sync", headers=auth)
    body = {"document_id": document["id"], "status": "synced", "crm": {"contact_id": "7"}}
    callback(n8n, body)
    callback(n8n, {**body, "status": "failed", "detail": "late"})
    result = n8n.get(f"/api/documents/{document['id']}", headers=auth).json()
    assert result["status"] == "synced" and len(result["sync_events"]) == 2


def test_failed_callback(n8n):
    auth = sign_in(n8n)
    document = uploaded(n8n, auth)
    n8n.post(f"/api/documents/{document['id']}/sync", headers=auth)
    callback(n8n, {"document_id": document["id"], "status": "failed", "detail": "HubSpot rejected the access token."})
    result = n8n.get(f"/api/documents/{document['id']}", headers=auth).json()
    assert result["status"] == "sync_failed" and result["error"] == "HubSpot rejected the access token." and result["can_sync"] is True


def test_callback_signature_is_required(n8n):
    auth = sign_in(n8n)
    document = uploaded(n8n, auth)
    n8n.post(f"/api/documents/{document['id']}/sync", headers=auth)
    body = {"document_id": document["id"], "status": "synced", "crm": {}}
    assert callback(n8n, body, secret="wrong").status_code == 401
    assert callback(n8n, body, now=time.time() - 600).status_code == 401
    assert n8n.post("/api/sync/callback", json=body).status_code == 401
    raw = json.dumps(body).encode()
    tampered = n8n.post("/api/sync/callback", content=raw.replace(b"synced", b"failed"), headers=signed_headers(SECRET, raw))
    assert tampered.status_code == 401
    assert n8n.get(f"/api/documents/{document['id']}", headers=auth).json()["status"] == "syncing"


def test_callback_with_no_secret_configured_is_always_refused(client, auth):
    document = uploaded(client, auth)
    raw = json.dumps({"document_id": document["id"], "status": "synced"}).encode()
    assert client.post("/api/sync/callback", content=raw, headers=signed_headers("", raw)).status_code == 401


def test_callback_for_an_unknown_document(n8n):
    assert callback(n8n, {"document_id": "00000000-0000-0000-0000-000000000000", "status": "synced"}).status_code == 404
    assert callback(n8n, {"document_id": "nope", "status": "synced"}).status_code == 404
    raw = b"not json"
    assert n8n.post("/api/sync/callback", content=raw, headers=signed_headers(SECRET, raw)).status_code == 400


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}
