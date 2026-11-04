import json

import httpx
import pytest

from app import crm
from app.crm import CrmError, HubSpot

RECORD = {
    "contact": {"email": "sara@brightpath.example", "firstname": "Sara"},
    "company": {"name": "Brightpath Studio", "domain": "brightpath.example"},
    "deal": {"dealname": "Invoice INV-1042 — Brightpath Studio", "amount": "1200.00", "currency": "USD", "closedate": "2026-12-02"},
    "note": "Net 30",
}


class FakeHubSpot:
    """Answers like HubSpot's REST API and records every call."""

    def __init__(self, companies=(), fail=None, stages=None):
        self.calls = []
        self.companies = list(companies)
        self.stages = stages
        self.fail = fail or {}
        self.next_id = 100

    def __call__(self, request):
        body = json.loads(request.content) if request.content else None
        key = (request.method, request.url.path)
        self.calls.append({"method": request.method, "path": request.url.path, "body": body, "auth": request.headers.get("authorization")})
        if self.fail.get(key):
            status = self.fail[key].pop(0)
            if not self.fail[key]:
                del self.fail[key]
            return httpx.Response(status, json={"status": "error", "message": "Property values were not valid"}, headers={"Retry-After": "0"})
        if request.url.path == "/crm/v3/pipelines/deals/default" and self.stages is not None:
            return httpx.Response(200, json={"id": "default", "stages": self.stages})
        if request.url.path == "/crm/v3/objects/contacts/batch/upsert":
            return httpx.Response(200, json={"status": "COMPLETE", "results": [{"id": "501", "properties": body["inputs"][0]["properties"]}]})
        if request.url.path == "/crm/v3/objects/companies/search":
            return httpx.Response(200, json={"total": len(self.companies), "results": [{"id": c} for c in self.companies]})
        if request.method == "POST":
            self.next_id += 1
            return httpx.Response(201, json={"id": str(self.next_id)})
        if request.method == "PATCH":
            return httpx.Response(200, json={"id": request.url.path.rsplit("/", 1)[1]})
        return httpx.Response(200, json={})

    def paths(self):
        return [(c["method"], c["path"]) for c in self.calls]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(crm.time, "sleep", lambda seconds: None)


def hubspot(fake, **kwargs):
    return HubSpot("pat-secret", client=httpx.Client(transport=httpx.MockTransport(fake)), **kwargs)


def test_first_sync_creates_everything_and_links_it():
    fake = FakeHubSpot()
    result = hubspot(fake, portal_id="4242").push(RECORD)
    assert result == {
        "contact_id": "501",
        "company_id": "101",
        "deal_id": "102",
        "note_id": "103",
        "contact_url": "https://app.hubspot.com/contacts/4242/record/0-1/501",
        "deal_url": "https://app.hubspot.com/contacts/4242/record/0-3/102",
    }
    assert fake.paths() == [
        ("POST", "/crm/v3/objects/contacts/batch/upsert"),
        ("POST", "/crm/v3/objects/companies/search"),
        ("POST", "/crm/v3/objects/companies"),
        ("GET", "/crm/v3/pipelines/deals/default"),
        ("POST", "/crm/v3/objects/deals"),
        ("POST", "/crm/v3/objects/notes"),
        ("PUT", "/crm/v4/objects/contacts/501/associations/default/companies/101"),
        ("PUT", "/crm/v4/objects/deals/102/associations/default/contacts/501"),
        ("PUT", "/crm/v4/objects/deals/102/associations/default/companies/101"),
        ("PUT", "/crm/v4/objects/notes/103/associations/default/contacts/501"),
    ]
    assert all(call["auth"] == "Bearer pat-secret" for call in fake.calls)


def test_request_bodies_match_hubspot():
    fake = FakeHubSpot()
    hubspot(fake, deal_stage="appointmentscheduled").push(RECORD)
    upsert, search, _, _, deal, note = (c["body"] for c in fake.calls[:6])
    assert upsert == {"inputs": [{"idProperty": "email", "id": "sara@brightpath.example", "properties": RECORD["contact"]}]}
    assert search["filterGroups"][0]["filters"] == [{"propertyName": "domain", "operator": "EQ", "value": "brightpath.example"}]
    assert deal["properties"] == {
        "dealname": "Invoice INV-1042 — Brightpath Studio",
        "amount": "1200.00",
        "dealstage": "appointmentscheduled",
        "pipeline": "default",
        "deal_currency_code": "USD",
        "closedate": "2026-12-02T00:00:00Z",
    }
    assert note["properties"]["hs_note_body"] == "Net 30"
    assert note["properties"]["hs_timestamp"].endswith("Z")


def test_an_existing_company_is_reused_and_updated():
    fake = FakeHubSpot(companies=["77"])
    result = hubspot(fake).push(RECORD)
    assert result["company_id"] == "77"
    assert ("PATCH", "/crm/v3/objects/companies/77") in fake.paths()
    assert ("POST", "/crm/v3/objects/companies") not in fake.paths()


def test_company_without_domain_is_searched_by_name():
    fake = FakeHubSpot()
    hubspot(fake).push(dict(RECORD, company={"name": "Cedar & Co"}))
    search = fake.calls[1]["body"]["filterGroups"][0]["filters"][0]
    assert search == {"propertyName": "name", "operator": "EQ", "value": "Cedar & Co"}


def test_resync_updates_the_same_records():
    fake = FakeHubSpot()
    existing = {"contact_id": "501", "company_id": "11", "deal_id": "12", "note_id": "13"}
    result = hubspot(fake).push(RECORD, existing=existing)
    assert result["deal_id"] == "12" and result["note_id"] == "13" and result["company_id"] == "11"
    assert [p for p in fake.paths() if p[0] in ("POST", "PATCH")] == [
        ("POST", "/crm/v3/objects/contacts/batch/upsert"),
        ("PATCH", "/crm/v3/objects/companies/11"),
        ("PATCH", "/crm/v3/objects/deals/12"),
        ("PATCH", "/crm/v3/objects/notes/13"),
    ]


def test_a_deal_deleted_in_hubspot_is_made_again():
    fake = FakeHubSpot(fail={("PATCH", "/crm/v3/objects/deals/12"): [404]})
    result = hubspot(fake).push(RECORD, existing={"deal_id": "12"})
    assert result["deal_id"] != "12"
    assert ("POST", "/crm/v3/objects/deals") in fake.paths()


def test_contact_only_record():
    fake = FakeHubSpot()
    result = hubspot(fake).push({"contact": {"email": "omar@gmail.com"}, "company": None, "deal": None, "note": None})
    assert result == {"contact_id": "501", "company_id": None, "deal_id": None, "note_id": None, "contact_url": None, "deal_url": None}
    assert fake.paths() == [("POST", "/crm/v3/objects/contacts/batch/upsert")]


def test_bad_token():
    fake = FakeHubSpot(fail={("POST", "/crm/v3/objects/contacts/batch/upsert"): [401]})
    with pytest.raises(CrmError) as caught:
        hubspot(fake).push(RECORD)
    assert caught.value.message == "HubSpot rejected the access token. Check HUBSPOT_TOKEN."
    assert caught.value.retryable is False


def test_rejected_record_shows_hubspots_message():
    fake = FakeHubSpot(fail={("POST", "/crm/v3/objects/deals"): [400]})
    with pytest.raises(CrmError, match="Property values were not valid"):
        hubspot(fake).push(RECORD)


def test_rate_limit_is_retried_once():
    fake = FakeHubSpot(fail={("POST", "/crm/v3/objects/contacts/batch/upsert"): [429]})
    assert hubspot(fake).push(RECORD)["contact_id"] == "501"


def test_repeated_server_errors_are_retryable():
    fake = FakeHubSpot(fail={("POST", "/crm/v3/objects/contacts/batch/upsert"): [500, 502]})
    with pytest.raises(CrmError) as caught:
        hubspot(fake).push(RECORD)
    assert caught.value.retryable is True


def test_network_failure_is_retryable():
    def down(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(CrmError) as caught:
        hubspot(down).push(RECORD)
    assert caught.value.retryable is True
    assert "pat-secret" not in caught.value.message


def test_base_url_can_point_at_a_mock():
    fake = FakeHubSpot()
    client = HubSpot("t", client=httpx.Client(transport=httpx.MockTransport(fake)), base_url="http://mock.local/")
    client.push({"contact": {"email": "a@b.co"}, "company": None, "deal": None, "note": None})
    assert fake.calls[0]["path"] == "/crm/v3/objects/contacts/batch/upsert"


NEW_ACCOUNT_STAGES = [
    {"id": "4561146603", "label": "Appointment scheduled", "metadata": {"isClosed": "false", "probability": "0.2"}},
    {"id": "4561146608", "label": "Closed won", "metadata": {"isClosed": "true", "probability": "1.0"}},
    {"id": "4561146609", "label": "Closed lost", "metadata": {"isClosed": "true", "probability": "0.0"}},
]


def deal_stage_sent(fake):
    return next(c["body"]["properties"]["dealstage"] for c in fake.calls if c["path"] == "/crm/v3/objects/deals")


def test_a_stage_name_is_turned_into_the_accounts_numeric_stage_id():
    # New HubSpot accounts reject "closedwon" and only accept their own stage IDs.
    fake = FakeHubSpot(stages=NEW_ACCOUNT_STAGES)
    hubspot(fake, deal_stage="closedwon").push(RECORD)
    assert deal_stage_sent(fake) == "4561146608"


def test_a_renamed_won_stage_is_still_found():
    stages = [dict(stage) for stage in NEW_ACCOUNT_STAGES]
    stages[1]["label"] = "Paid"
    fake = FakeHubSpot(stages=stages)
    hubspot(fake, deal_stage="closedwon").push(RECORD)
    assert deal_stage_sent(fake) == "4561146608"


def test_a_stage_id_is_used_as_it_is():
    fake = FakeHubSpot(stages=NEW_ACCOUNT_STAGES)
    hubspot(fake, deal_stage="4561146603").push(RECORD)
    assert deal_stage_sent(fake) == "4561146603"


def test_an_unreadable_pipeline_falls_back_to_the_configured_stage():
    fake = FakeHubSpot(fail={("GET", "/crm/v3/pipelines/deals/default"): [403]})
    hubspot(fake, deal_stage="closedwon").push(RECORD)
    assert deal_stage_sent(fake) == "closedwon"


def test_the_pipeline_is_read_once_per_client():
    fake = FakeHubSpot(stages=NEW_ACCOUNT_STAGES)
    client = hubspot(fake)
    client.push(RECORD)
    client.push(RECORD, existing={"deal_id": "102"})
    assert fake.paths().count(("GET", "/crm/v3/pipelines/deals/default")) == 1


def test_find_matches_skips_searches_it_has_nothing_to_search_with():
    fake = FakeHubSpot(companies=["12"])
    found = hubspot(fake, portal_id="42").find_matches({"contact": {"email": None}, "company": {"name": "Brightpath Studio"}, "deal": None})
    assert fake.paths() == [("POST", "/crm/v3/objects/companies/search")]
    assert fake.calls[0]["body"]["filterGroups"][0]["filters"][0] == {"propertyName": "name", "operator": "EQ", "value": "Brightpath Studio"}
    assert found["contact"] is None and found["deal"] is None
    assert found["company"]["id"] == "12" and found["company"]["url"] == "https://app.hubspot.com/contacts/42/record/0-2/12"
