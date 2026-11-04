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
from app.main import verify_google_token
from app.sync import SIGNATURE_HEADER, TIMESTAMP_HEADER, signed_headers
from conftest import EXTRACTION, N8N_URL, SECRET, FakeGoogle, make_settings, sign_in

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


@pytest.fixture
def mail_client(build, mailer):
    return build(send_email=mailer)


def reset_token(email):
    return email.url.split("token=", 1)[1]


def forgot(client, email="sara@example.com"):
    return client.post("/api/password/forgot", json={"email": email})


def test_reset_is_hidden_and_refused_without_email_settings(client):
    assert client.get("/api/auth/options").json()["password_reset"] is False
    assert forgot(client).status_code == 503


def test_reset_by_email_sets_a_new_password_and_ends_sessions(mail_client, mailer):
    auth = sign_in(mail_client)
    earlier = old_token(mail_client, auth)
    assert mail_client.get("/api/auth/options").json()["password_reset"] is True
    response = forgot(mail_client, "Sara@Example.com")
    assert response.json() == {"detail": "If that email has an account, we've sent a reset link."}
    [email] = mailer.sent
    assert email.to == "sara@example.com" and email.url.startswith("http://localhost:5180/reset-password?token=")
    reset = mail_client.post("/api/password/reset", json={"token": reset_token(email), "new_password": "a-brand-new-secret"})
    assert reset.status_code == 200
    assert mail_client.get("/api/me", headers=earlier).status_code == 401
    login = lambda password: mail_client.post("/api/auth/login", json={"email": "sara@example.com", "password": password}).status_code
    assert login("correct-horse-battery") == 401 and login("a-brand-new-secret") == 200


def test_reset_links_work_once_and_only_the_newest(mail_client, mailer):
    sign_in(mail_client)
    forgot(mail_client)
    forgot(mail_client)
    first, second = (reset_token(email) for email in mailer.sent)
    use = lambda token: mail_client.post("/api/password/reset", json={"token": token, "new_password": "a-brand-new-secret"})
    assert use(first).json() == {"detail": "This reset link is invalid or has expired."}
    assert use(second).status_code == 200
    assert use(second).status_code == 400
    assert use("made-up-token").status_code == 400


def test_reset_links_expire(mail_client, mailer, database_url):
    sign_in(mail_client)
    forgot(mail_client)
    with psycopg.connect(database_url, autocommit=True) as db:
        db.execute("UPDATE password_reset_tokens SET expires_at = now() - interval '1 second'")
    response = mail_client.post("/api/password/reset", json={"token": reset_token(mailer.sent[0]), "new_password": "a-brand-new-secret"})
    assert response.status_code == 400


def test_reset_does_not_reveal_which_emails_have_accounts(mail_client, mailer):
    sign_in(mail_client)
    demo = mail_client.post("/api/auth/demo").json()["user"]["email"]
    replies = {forgot(mail_client, email).text for email in ("nobody@example.com", demo, "sara@example.com")}
    assert len(replies) == 1
    assert [email.to for email in mailer.sent] == ["sara@example.com"]


def test_reset_emails_are_limited_per_address_without_saying_so(mail_client, mailer):
    sign_in(mail_client)
    replies = [forgot(mail_client) for _ in range(4)]
    assert {r.status_code for r in replies} == {200} and len({r.text for r in replies}) == 1
    assert len(mailer.sent) == 3


def test_reset_requests_are_limited_per_ip(mail_client):
    codes = [forgot(mail_client, f"person{n}@example.com").status_code for n in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429


def test_a_failed_email_still_gets_the_same_reply(mail_client, mailer):
    sign_in(mail_client)
    mailer.error = OSError("SMTP is down")
    response = forgot(mail_client)
    assert response.status_code == 200 and response.json()["detail"].startswith("If that email has an account")


def test_reset_needs_a_long_enough_password(mail_client, mailer):
    sign_in(mail_client)
    forgot(mail_client)
    response = mail_client.post("/api/password/reset", json={"token": reset_token(mailer.sent[0]), "new_password": "tiny7ch"})
    assert response.status_code == 422 and "tiny7ch" not in response.text


GOOGLE_ID = "123-test.apps.googleusercontent.com"


@pytest.fixture
def google():
    return FakeGoogle()


@pytest.fixture
def google_client(build, google):
    return build(verify_google=google, google_client_id=GOOGLE_ID)


def google_sign_in(client, credential="sara@example.com"):
    return client.post("/api/auth/google", json={"credential": credential})


def test_google_is_off_without_a_client_id(client):
    assert client.get("/api/auth/options").json()["google"] is False
    assert google_sign_in(client).status_code == 404


def test_google_creates_an_account_without_a_password(google_client, google):
    assert google_client.get("/api/auth/options").json()["google"] is True
    response = google_sign_in(google_client, "Sara@Example.com")
    assert response.status_code == 200, response.text
    user = response.json()["user"]
    assert user["email"] == "sara@example.com" and user["has_password"] is False
    assert google.calls == [("Sara@Example.com", GOOGLE_ID)]
    # Signing in again finds the same account.
    assert google_sign_in(google_client, "sara@example.com").json()["user"]["id"] == user["id"]
    # With no password yet, one can be set without a current password.
    auth = {"Authorization": "Bearer " + response.json()["token"]}
    assert google_client.post("/api/me/password", json={"new_password": "a-brand-new-secret"}, headers=auth).status_code == 200
    assert google_client.post("/api/auth/login", json={"email": "sara@example.com", "password": "a-brand-new-secret"}).status_code == 200


def test_google_takes_over_an_unverified_signup_and_drops_its_password(google_client):
    # Someone registered this address with a password, without proving they own it.
    squatter = old_token(google_client, sign_in(google_client, "sara@example.com"))
    owner = google_sign_in(google_client).json()
    assert owner["user"]["has_password"] is False
    assert google_client.get("/api/me", headers=squatter).status_code == 401
    assert google_client.post("/api/auth/login", json={"email": "sara@example.com", "password": "correct-horse-battery"}).status_code == 401


def test_google_keeps_the_password_of_a_verified_account(google_client, mailer, build, google):
    mail_client = build(send_email=mailer, verify_google=google, google_client_id=GOOGLE_ID)
    sign_in(mail_client)
    forgot(mail_client)
    mail_client.post("/api/password/reset", json={"token": reset_token(mailer.sent[0]), "new_password": "a-brand-new-secret"})
    assert google_sign_in(mail_client).json()["user"]["has_password"] is True
    assert mail_client.post("/api/auth/login", json={"email": "sara@example.com", "password": "a-brand-new-secret"}).status_code == 200


def test_google_refuses_bad_tokens_and_unconfirmed_emails(google_client, google):
    assert google_sign_in(google_client, "forged").json() == {"detail": "Google sign-in could not be verified. Please try again."}
    google.claims["unverified"] = {"email": "sara@example.com", "email_verified": False}
    google.claims["no-email"] = {"email_verified": True}
    assert google_sign_in(google_client, "unverified").json() == {"detail": "Google did not confirm this email address."}
    assert google_sign_in(google_client, "no-email").status_code == 401
    assert google_client.post("/api/auth/google", json={"credential": "x" * 8001}).status_code == 422


def test_google_never_opens_a_demo_account(google_client):
    demo = google_client.post("/api/auth/demo").json()["user"]["email"]
    assert google_sign_in(google_client, demo).status_code == 403


def test_google_sign_in_is_rate_limited_like_passwords(google_client):
    codes = [google_sign_in(google_client, "forged").status_code for _ in range(21)]
    assert codes[:20] == [401] * 20 and codes[20] == 429


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


# What's already in HubSpot


def test_matches_finds_nothing_for_a_new_contact(client, auth, services):
    document = uploaded(client, auth)
    found = client.get(f"/api/documents/{document['id']}/matches", headers=auth).json()
    assert found == {"available": True, "contact": None, "company": None, "deal": None}
    searches = [r for r in services.requests if r.path.endswith("/search")]
    assert [r.path for r in searches] == ["/crm/v3/objects/contacts/search", "/crm/v3/objects/companies/search"]
    assert searches[0].body["filterGroups"][0]["filters"][0] == {"propertyName": "email", "operator": "EQ", "value": "omar@kestrel.example"}
    # The company is looked up by the domain of a work email.
    assert searches[1].body["filterGroups"][0]["filters"][0]["value"] == "kestrel.example"


def test_matches_shows_existing_records_until_this_document_owns_them(client, auth, services):
    services.found = {
        "contacts": [{"id": "900", "properties": {"firstname": "Omar", "lastname": "Siddiqui", "email": "omar@kestrel.example"}, "updatedAt": "2026-03-03T10:00:00Z"}],
        "companies": [{"id": "77", "properties": {"name": "Kestrel"}, "updatedAt": "2026-02-01T09:00:00Z"}],
    }
    document = uploaded(client, auth)
    found = client.get(f"/api/documents/{document['id']}/matches", headers=auth).json()
    assert found["contact"] == {
        "id": "900", "name": "Omar Siddiqui", "updated_at": "2026-03-03T10:00:00Z",
        "url": "https://app.hubspot.com/contacts/4242/record/0-1/900", "own": False,
    }
    assert found["company"]["name"] == "Kestrel" and found["company"]["own"] is False
    client.post(f"/api/documents/{document['id']}/sync", headers=auth)
    after = client.get(f"/api/documents/{document['id']}/matches", headers=auth).json()
    # The fake upserts the contact as 900, the record it already found.
    assert after["contact"]["own"] is True


def test_matches_spots_an_invoice_already_sent_as_a_deal(client, auth, gemini, services):
    gemini.replies = [dict(EXTRACTION, doc_type="invoice", fields=EXTRACTION["fields"] + [
        {"key": "invoice.number", "value": "INV-7", "source": "INV-7", "confidence": 0.99},
        {"key": "invoice.total", "value": "$90.00", "source": "$90.00", "confidence": 0.99},
    ])]
    services.found = {"deals": [{"id": "31", "properties": {"dealname": "Invoice INV-7 — Kestrel Logistics", "amount": "90.00"}, "updatedAt": "2026-03-03T10:00:00Z"}]}
    document = uploaded(client, auth)
    found = client.get(f"/api/documents/{document['id']}/matches", headers=auth).json()
    assert found["deal"]["name"] == "Invoice INV-7 — Kestrel Logistics" and found["deal"]["amount"] == "90.00"
    deal_search = next(r for r in services.requests if r.path == "/crm/v3/objects/deals/search")
    assert deal_search.body["filterGroups"][0]["filters"][0]["value"] == "Invoice INV-7 — Kestrel Logistics"


def test_matches_is_unavailable_without_a_token_or_when_hubspot_fails(build, client, auth, services):
    document = uploaded(client, auth)
    services.hubspot_status = 401
    assert client.get(f"/api/documents/{document['id']}/matches", headers=auth).json() == {"available": False}
    n8n_only = build(hubspot_token="", n8n_webhook_url=N8N_URL, n8n_shared_secret=SECRET)
    other = sign_in(n8n_only, email="lena@example.com")
    mine = uploaded(n8n_only, other, data=PNG + b"2")
    services.requests.clear()
    assert n8n_only.get(f"/api/documents/{mine['id']}/matches", headers=other).json() == {"available": False}
    assert services.requests == []


def test_matches_only_for_your_own_documents(client, auth):
    document = uploaded(client, auth)
    stranger = sign_in(client, email="mallory@example.com")
    assert client.get(f"/api/documents/{document['id']}/matches", headers=stranger).status_code == 404


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


def test_the_real_google_check_turns_a_malformed_token_into_value_error():
    # A malformed token fails before any keys are fetched, so this stays offline.
    with pytest.raises(ValueError):
        verify_google_token("not-a-jwt", GOOGLE_ID)
