"""Only teams owned by an account in CRM_OWNER_EMAILS reach the real CRM. Everyone else, and every demo, practises."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from conftest import make_settings, refuse_google, sign_in
from fakes import FakePipedrive, FakeSheets, service_account

OWNER = "owner@inkfold.example"


@pytest.fixture
def pipedrive():
    return FakePipedrive()


@pytest.fixture
def sheets():
    return FakeSheets()


@pytest.fixture
def make(store, gemini, services, pipedrive, sheets, database_url):
    account = service_account()

    def route(request):
        if request.url.host == "api.pipedrive.com":
            return pipedrive(request)
        if request.url.host.endswith("googleapis.com"):
            return sheets(request)
        return services(request)

    def _make(owners=(OWNER,), **overrides):
        settings = make_settings(
            database_url,
            crm_owner_emails=list(owners),
            pipedrive_token="pd-test",
            pipedrive_domain="acme",
            sheets={"service_account": account, "spreadsheet_id": "sheet-1", "tab": "Inkfold"},
            **overrides,
        )
        app = create_app(settings, store=store, gemini_client=gemini, http_client=httpx.Client(transport=httpx.MockTransport(route)),
                         verify_google=refuse_google)
        return TestClient(app)

    return _make


def upload(client, auth, data=b"\x89PNG\r\n\x1a\ncard-bytes"):
    response = client.post("/api/documents", files={"file": ("card.png", data, "application/octet-stream")}, headers=auth)
    assert response.status_code == 201, response.text
    return response.json()


def send(client, auth, document):
    response = client.post(f"/api/documents/{document['id']}/sync", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def outside_calls(services, pipedrive, sheets):
    return services.requests + pipedrive.requests + sheets.rows


def test_a_stranger_practises_and_nothing_reaches_hubspot(make, services, pipedrive, sheets):
    client = make()
    auth = sign_in(client, "stranger@example.com")
    assert client.get("/api/me", headers=auth).json()["practice_sends"] is True
    sent = send(client, auth, upload(client, auth))
    assert sent["status"] == "synced"
    assert sent["crm"]["simulated"] is True and sent["crm"]["destination"] == "hubspot"
    assert sent["crm"]["contact_id"].startswith("demo-") and "contact_url" not in sent["crm"]
    event = sent["sync_events"][-1]
    assert event["channel"] == "practice"
    assert "nothing left Inkfold" in event["detail"] and "omar@kestrel.example" in event["detail"]
    assert outside_calls(services, pipedrive, sheets) == []


def test_the_listed_owner_sends_for_real(make, services):
    client = make()
    auth = sign_in(client, OWNER.upper())
    assert client.get("/api/me", headers=auth).json()["practice_sends"] is False
    sent = send(client, auth, upload(client, auth))
    assert sent["status"] == "synced" and "simulated" not in sent["crm"]
    assert sent["sync_events"][-1]["channel"] == "direct"
    assert services.hubspot_calls()


def test_a_teammate_of_the_owner_sends_for_real(make, services):
    client = make()
    owner = sign_in(client, OWNER)
    member = sign_in(client, "omar@example.com")
    link = client.post("/api/team/invites", json={"email": "omar@example.com"}, headers=owner).json()["invite"]["link"]
    client.post(f"/api/invites/{link.rsplit('/', 1)[1]}/accept", headers=member)
    sent = send(client, member, upload(client, member))
    assert "simulated" not in sent["crm"] and services.hubspot_calls()


def test_a_demo_always_practises_even_when_everyone_else_is_trusted(make, services, pipedrive, sheets):
    client = make(owners=["*"])
    session = client.post("/api/auth/demo").json()
    auth = {"Authorization": "Bearer " + session["token"]}
    assert session["user"]["practice_sends"] is True
    listed = client.get("/api/documents", headers=auth).json()["documents"]
    documents = [client.get(f"/api/documents/{d['id']}", headers=auth).json() for d in listed]
    ready = [d for d in documents if d["can_sync"]]
    assert ready, "the demo has documents ready to send"
    for document in ready:
        client.get(f"/api/documents/{document['id']}/matches", headers=auth)
        assert send(client, auth, document)["crm"]["simulated"] is True
    assert outside_calls(services, pipedrive, sheets) == []


def test_without_the_setting_nobody_reaches_the_crm(make, services):
    client = make(owners=())
    auth = sign_in(client, OWNER)
    assert send(client, auth, upload(client, auth))["crm"]["simulated"] is True
    assert services.requests == []


def test_practice_look_ups_only_find_the_teams_own_practice_sends(make, services):
    client = make()
    sara = sign_in(client, "sara@example.com")
    first = send(client, sara, upload(client, sara))
    second = upload(client, sara, data=b"\x89PNG\r\n\x1a\nanother-card")
    found = client.get(f"/api/documents/{second['id']}/matches", headers=sara).json()
    assert found["available"] is True
    assert found["contact"]["id"] == first["crm"]["contact_id"] and found["contact"]["own"] is False
    assert found["company"]["name"] == "Kestrel Logistics" and found["contact"]["url"] is None
    # The first document's own records count as its own, so they aren't a surprise.
    again = client.get(f"/api/documents/{first['id']}/matches", headers=sara).json()
    assert again["contact"] is None or again["contact"]["own"] is True

    omar = sign_in(client, "omar@example.com")
    theirs = upload(client, omar)
    nothing = client.get(f"/api/documents/{theirs['id']}/matches", headers=omar).json()
    assert nothing["contact"] is None and nothing["company"] is None
    assert services.requests == []


def test_a_resend_keeps_the_practice_ids(make):
    client = make()
    auth = sign_in(client, "sara@example.com")
    first = send(client, auth, upload(client, auth))
    edited = client.patch(f"/api/documents/{first['id']}/fields", json={"fields": {"contact.phone": "+1 555 0100"}}, headers=auth)
    assert edited.status_code == 200, edited.text
    again = send(client, auth, edited.json())
    assert again["crm"]["contact_id"] == first["crm"]["contact_id"]


def test_pipedrive_and_sheets_practise_too(make, services, pipedrive, sheets):
    client = make()
    auth = sign_in(client, "sara@example.com")
    for destination, label in (("pipedrive", "Pipedrive"), ("sheets", "Google Sheets")):
        assert client.patch("/api/team/destination", json={"destination": destination}, headers=auth).status_code == 200
        document = upload(client, auth, data=b"\x89PNG\r\n\x1a\n" + destination.encode())
        sent = send(client, auth, document)
        assert sent["crm"]["simulated"] is True and sent["crm"]["destination"] == destination
        assert label in sent["sync_events"][-1]["detail"]
    assert outside_calls(services, pipedrive, sheets) == []


def test_what_was_only_practised_can_be_sent_for_real_once_the_owner_is_listed(make, services):
    auth = sign_in(make(owners=()), OWNER)
    document = upload(make(owners=()), auth)
    practised = send(make(owners=()), auth, document)
    assert practised["crm"]["simulated"] is True

    real = make()
    fresh = real.get(f"/api/documents/{document['id']}", headers=auth).json()
    assert fresh["can_sync"] is True
    sent = send(real, auth, fresh)
    assert "simulated" not in sent["crm"] and services.hubspot_calls()
