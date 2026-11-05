import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from conftest import make_settings, refuse_google, sign_in
from fakes import FakePipedrive, FakeSheets, service_account

PNG = b"\x89PNG\r\n\x1a\n" + b"card-bytes"
INVOICE = {
    "is_business_document": True,
    "doc_type": "invoice",
    "fields": [
        {"key": "contact.email", "value": "sara@brightpath.example.com", "source": "x", "confidence": 0.99},
        {"key": "company.name", "value": "Brightpath Studio", "source": "x", "confidence": 0.99},
        {"key": "invoice.number", "value": "INV-1042", "source": "x", "confidence": 0.99},
        {"key": "invoice.total", "value": "$1,249.19", "source": "x", "confidence": 0.99},
    ],
    "line_items": [],
}


@pytest.fixture
def pipedrive():
    return FakePipedrive()


@pytest.fixture
def sheets():
    return FakeSheets()


@pytest.fixture
def account():
    return service_account()


@pytest.fixture
def make(store, gemini, services, pipedrive, sheets, database_url, account):
    def route(request):
        if request.url.host == "api.pipedrive.com":
            return pipedrive(request)
        if request.url.host.endswith("googleapis.com"):
            return sheets(request)
        return services(request)

    def _make(**overrides):
        settings = make_settings(
            database_url,
            pipedrive_token="pd-test",
            pipedrive_domain="acme",
            sheets={"service_account": account, "spreadsheet_id": "sheet-1", "tab": "Inkfold"},
        )
        settings.update(overrides)
        app = create_app(settings, store=store, gemini_client=gemini, http_client=httpx.Client(transport=httpx.MockTransport(route)),
                         verify_google=refuse_google)
        return TestClient(app)

    return _make


@pytest.fixture
def client(make):
    return make()


def upload(client, auth, data=PNG, name="card.png"):
    response = client.post("/api/documents", files={"file": (name, data, "application/octet-stream")}, headers=auth)
    assert response.status_code == 201, response.text
    return response.json()


def choose(client, auth, destination):
    response = client.patch("/api/team/destination", json={"destination": destination}, headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def send(client, auth, document):
    return client.post(f"/api/documents/{document['id']}/sync", headers=auth).json()


def test_the_team_chooses_from_the_destinations_the_server_has(client, make):
    auth = sign_in(client)
    team = client.get("/api/team", headers=auth).json()
    assert [d["key"] for d in team["destinations"]] == ["hubspot", "pipedrive", "sheets"]
    assert team["destination"] == "hubspot"
    assert choose(client, auth, "sheets")["destination"] == "sheets"
    only_hubspot = make(pipedrive_token="", sheets=None)
    assert client.get("/api/team", headers=auth).json()["destination"] == "sheets"
    assert only_hubspot.get("/api/team", headers=auth).json()["destination"] == "hubspot"
    refused = only_hubspot.patch("/api/team/destination", json={"destination": "pipedrive"}, headers=auth)
    assert refused.status_code == 400 and "isn't set up" in refused.json()["detail"]
    assert client.patch("/api/team/destination", json={"destination": "salesforce"}, headers=auth).status_code == 422


def test_only_the_owner_chooses(client):
    owner = sign_in(client, "sara@example.com")
    member = sign_in(client, "omar@example.com")
    link = client.post("/api/team/invites", json={"email": "omar@example.com"}, headers=owner).json()["invite"]["link"]
    client.post(f"/api/invites/{link.rsplit('/', 1)[1]}/accept", headers=member)
    assert client.patch("/api/team/destination", json={"destination": "pipedrive"}, headers=member).status_code == 403


def test_pipedrive_gets_a_person_organization_and_note(client, pipedrive):
    auth = sign_in(client)
    choose(client, auth, "pipedrive")
    document = upload(client, auth)
    assert document["destination"] == {"key": "pipedrive", "label": "Pipedrive"} and document["can_sync"] is True
    sent = send(client, auth, document)
    assert sent["status"] == "synced", sent
    person = pipedrive.records["persons"][int(sent["crm"]["contact_id"])]
    org = pipedrive.records["organizations"][int(sent["crm"]["company_id"])]
    assert person["name"] == "Omar Siddiqui" and person["email"][0]["value"] == "omar@kestrel.example"
    assert person["org_id"] == org["id"] and org["name"] == "Kestrel Logistics"
    note = pipedrive.records["notes"][int(sent["crm"]["note_id"])]
    assert "Imported by Inkfold from card.png." in note["content"] and note["person_id"] == person["id"]
    assert sent["crm"]["destination"] == "pipedrive"
    assert sent["crm"]["contact_url"] == f"https://acme.pipedrive.com/person/{person['id']}"
    assert sent["sync_events"][-1]["channel"] == "pipedrive"
    assert sent["sync_events"][-1]["detail"].startswith(f"Saved in Pipedrive: person {person['id']}, organization {org['id']}")
    assert all(r.headers["x-api-token"] == "pd-test" for r in pipedrive.requests)


def test_pipedrive_reuses_a_person_with_the_same_email_and_updates_on_resend(client, pipedrive):
    pipedrive.records["persons"][7] = {"id": 7, "name": "Omar S", "email": [{"value": "omar@kestrel.example"}]}
    auth = sign_in(client)
    choose(client, auth, "pipedrive")
    document = upload(client, auth)
    first = send(client, auth, document)
    assert first["crm"]["contact_id"] == "7" and pipedrive.records["persons"][7]["name"] == "Omar Siddiqui"
    client.patch(f"/api/documents/{document['id']}/fields", json={"fields": {"contact.job_title": "COO"}}, headers=auth)
    pipedrive.requests.clear()
    again = send(client, auth, document)
    assert {k: again["crm"][k] for k in ("contact_id", "company_id", "note_id")} == {k: first["crm"][k] for k in ("contact_id", "company_id", "note_id")}
    assert [r.method for r in pipedrive.requests] == ["PUT", "PUT", "PUT"]
    assert "Job title: COO" in pipedrive.records["notes"][int(again["crm"]["note_id"])]["content"]


def test_pipedrive_invoices_become_won_deals(client, pipedrive, gemini):
    gemini.replies.append(INVOICE)
    auth = sign_in(client)
    choose(client, auth, "pipedrive")
    sent = send(client, auth, upload(client, auth, data=b"%PDF-1.7 invoice", name="invoice.pdf"))
    deal = pipedrive.records["deals"][int(sent["crm"]["deal_id"])]
    assert deal["title"] == "Invoice INV-1042 — Brightpath Studio" and deal["value"] == 1249.19
    assert deal["currency"] == "USD" and deal["status"] == "won"


def test_pipedrive_errors_are_shown(client, pipedrive):
    pipedrive.status = 401
    auth = sign_in(client)
    choose(client, auth, "pipedrive")
    sent = send(client, auth, upload(client, auth))
    assert sent["status"] == "sync_failed" and "PIPEDRIVE_API_TOKEN" in sent["error"]


def test_pipedrive_matches_show_what_a_send_would_touch(client, pipedrive):
    pipedrive.records["persons"][7] = {"id": 7, "name": "Omar S", "email": [{"value": "omar@kestrel.example"}]}
    auth = sign_in(client)
    choose(client, auth, "pipedrive")
    document = upload(client, auth)
    found = client.get(f"/api/documents/{document['id']}/matches", headers=auth).json()
    assert found["available"] is True and found["destination"] == "pipedrive"
    assert found["contact"] == {"id": "7", "name": "Omar S", "updated_at": None, "url": "https://acme.pipedrive.com/person/7", "own": False}
    assert found["company"] is None


def test_a_sheet_gets_a_header_and_one_row_per_document(client, sheets, gemini):
    gemini.replies.append({**INVOICE, "fields": [f for f in INVOICE["fields"] if f["key"] != "contact.email"]})
    auth = sign_in(client)
    choose(client, auth, "sheets")
    document = upload(client, auth, data=b"%PDF-1.7 invoice", name="invoice.pdf")
    # A sheet doesn't match people by email, so a document without one can still go.
    assert document["missing"] == [] and document["can_sync"] is True
    sent = send(client, auth, document)
    assert sent["status"] == "synced", sent
    assert sheets.tab == "Inkfold" and sheets.rows[0][:3] == ["Inkfold ID", "Sent at", "Type"]
    row = dict(zip(sheets.rows[0], sheets.rows[1]))
    assert row["Inkfold ID"] == document["id"] and row["Type"] == "Invoice" and row["Company"] == "Brightpath Studio"
    assert row["Total"] == 1249.19 and row["Email"] == ""
    assert sent["crm"] == {"row": 2, "sheet_url": "https://docs.google.com/spreadsheets/d/sheet-1/edit", "destination": "sheets"}
    assert sent["sync_events"][-1]["detail"] == "Saved to Google Sheets, row 2."
    assert all(r.params.get("valueInputOption") in (None, "RAW") for r in sheets.requests)


def test_sending_again_updates_the_same_row_even_after_rows_move(client, sheets):
    auth = sign_in(client)
    choose(client, auth, "sheets")
    document = upload(client, auth)
    send(client, auth, document)
    # Someone adds a row above it in the sheet.
    sheets.rows.insert(1, ["something else"])
    client.patch(f"/api/documents/{document['id']}/fields", json={"fields": {"contact.job_title": "COO"}}, headers=auth)
    again = send(client, auth, document)
    assert again["crm"]["row"] == 3 and len(sheets.rows) == 3
    assert dict(zip(sheets.rows[0], sheets.rows[2]))["Job title"] == "COO"
    assert sheets.tokens == 1


def test_formulas_in_a_document_stay_text(client, sheets, gemini):
    gemini.replies.append({**INVOICE, "fields": INVOICE["fields"] + [
        {"key": "notes", "value": '=HYPERLINK("http://evil.example","x")', "source": "x", "confidence": 0.9}]})
    auth = sign_in(client)
    choose(client, auth, "sheets")
    send(client, auth, upload(client, auth, data=b"%PDF-1.7 f", name="f.pdf"))
    writes = [r for r in sheets.requests if r.method in ("PUT", "POST") and r.body and "values" in r.body]
    assert writes and all(r.params["valueInputOption"] == "RAW" for r in writes)


def test_sheet_permission_problems_name_the_service_account(client, sheets):
    sheets.status = 403
    auth = sign_in(client)
    choose(client, auth, "sheets")
    sent = send(client, auth, upload(client, auth))
    assert sent["status"] == "sync_failed" and "inkfold@demo.iam.gserviceaccount.com" in sent["error"]


def test_a_broken_service_account_key_is_reported(make):
    client = make(sheets={"service_account": "{not json", "spreadsheet_id": "sheet-1", "tab": "Inkfold"})
    auth = sign_in(client)
    choose(client, auth, "sheets")
    sent = send(client, auth, upload(client, auth))
    assert sent["status"] == "sync_failed" and "GOOGLE_SERVICE_ACCOUNT_JSON" in sent["error"]


def test_a_document_sent_to_hubspot_can_also_go_to_a_new_destination(client, pipedrive):
    auth = sign_in(client)
    document = upload(client, auth)
    first = send(client, auth, document)
    assert first["status"] == "synced" and first["crm"]["destination"] == "hubspot" and first["can_sync"] is False
    choose(client, auth, "pipedrive")
    moved = client.get(f"/api/documents/{document['id']}", headers=auth).json()
    assert moved["can_sync"] is True
    sent = send(client, auth, document)
    assert sent["crm"]["destination"] == "pipedrive" and sent["status"] == "synced"
    # The HubSpot ids weren't sent to Pipedrive as if they were its own.
    assert all(r.method == "POST" for r in pipedrive.requests if r.path.split("/")[-1] != "search")


def test_no_destination_at_all(make):
    client = make(hubspot_token="", pipedrive_token="", sheets=None)
    auth = sign_in(client)
    document = upload(client, auth)
    assert document["can_sync"] is False and document["destination"] is None
    response = client.post(f"/api/documents/{document['id']}/sync", headers=auth)
    assert response.status_code == 503 and "Nothing to send to" in response.json()["detail"]
