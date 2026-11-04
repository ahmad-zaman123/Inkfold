"""Creates or updates the HubSpot records for one document."""

import logging
import time
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10
MAX_RETRY_WAIT_SECONDS = 2
OBJECT_TYPE_IDS = {"contacts": "0-1", "deals": "0-3"}


class CrmError(Exception):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.message = message
        self.retryable = retryable


class _NotFound(Exception):
    pass


class HubSpot:
    BASE = "https://api.hubapi.com"

    def __init__(self, token, portal_id=None, deal_stage="closedwon", client=None, base_url=None):
        self.token = token
        self.portal_id = portal_id
        self.deal_stage = deal_stage
        self._stage_id = None
        self.client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
        self.base = (base_url or self.BASE).rstrip("/")

    def _request(self, method, path, body=None):
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        for attempt in (1, 2):
            try:
                response = self.client.request(method, self.base + path, json=body, headers=headers, timeout=TIMEOUT_SECONDS)
            except httpx.HTTPError as error:
                if attempt == 2:
                    raise CrmError("We couldn't reach HubSpot. Please try again.", retryable=True) from error
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 2:
                    raise CrmError("HubSpot is busy right now. Please try again in a minute.", retryable=True)
                time.sleep(_retry_after(response))
                continue
            if response.status_code in (401, 403):
                raise CrmError("HubSpot rejected the access token. Check HUBSPOT_TOKEN.")
            if response.status_code == 404:
                raise _NotFound(path)
            if response.status_code >= 400:
                message = _error_message(response)
                logger.warning("HubSpot %s %s failed: %s", method, path, message)
                raise CrmError(f"HubSpot didn't accept the record: {message}")
            return response.json() if response.content else {}

    def _create_or_update(self, object_type, existing_id, properties):
        # A record deleted in HubSpot since the last sync is simply made again.
        if existing_id:
            try:
                return self._request("PATCH", f"/crm/v3/objects/{object_type}/{existing_id}", {"properties": properties})["id"]
            except _NotFound:
                pass
        return self._request("POST", f"/crm/v3/objects/{object_type}", {"properties": properties})["id"]

    def _associate(self, from_type, from_id, to_type, to_id):
        self._request("PUT", f"/crm/v4/objects/{from_type}/{from_id}/associations/default/{to_type}/{to_id}")

    def _contact(self, contact):
        body = {"inputs": [{"idProperty": "email", "id": contact["email"], "properties": contact}]}
        return self._request("POST", "/crm/v3/objects/contacts/batch/upsert", body)["results"][0]["id"]

    def _company(self, company, existing_id):
        if existing_id:
            return self._create_or_update("companies", existing_id, company)
        prop, value = ("domain", company["domain"]) if company.get("domain") else ("name", company["name"])
        found = self._request(
            "POST",
            "/crm/v3/objects/companies/search",
            {"filterGroups": [{"filters": [{"propertyName": prop, "operator": "EQ", "value": value}]}], "limit": 1},
        )
        if found.get("results"):
            company_id = found["results"][0]["id"]
            self._request("PATCH", f"/crm/v3/objects/companies/{company_id}", {"properties": company})
            return company_id
        return self._create_or_update("companies", None, company)

    def _resolve_stage(self):
        """The stage ID to use. Newer HubSpot accounts use numeric stage IDs, so `closedwon` is looked up by label."""
        if self._stage_id is None:
            self._stage_id = self.deal_stage
            try:
                stages = self._request("GET", "/crm/v3/pipelines/deals/default").get("stages") or []
            except (CrmError, _NotFound):
                stages = []  # Can't read the pipeline: send the configured value as it is.
            self._stage_id = _match_stage(self.deal_stage, stages) or self.deal_stage
        return self._stage_id

    def _deal(self, deal, existing_id):
        properties = {
            "dealname": deal["dealname"],
            "amount": deal["amount"],
            "dealstage": self._resolve_stage(),
            "pipeline": "default",
        }
        if deal.get("currency"):
            properties["deal_currency_code"] = deal["currency"]
        if deal.get("closedate"):
            properties["closedate"] = deal["closedate"] + "T00:00:00Z"
        return self._create_or_update("deals", existing_id, properties)

    def _note(self, body, existing_id):
        properties = {"hs_note_body": body, "hs_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        return self._create_or_update("notes", existing_id, properties)

    def _url(self, object_type, object_id):
        if not self.portal_id or not object_id:
            return None
        return f"https://app.hubspot.com/contacts/{self.portal_id}/record/{OBJECT_TYPE_IDS[object_type]}/{object_id}"

    def push(self, record, existing=None):
        """Sends `record`. Passing the last result updates the same deal and note instead of adding new ones."""
        existing = existing or {}
        try:
            contact_id = self._contact(record["contact"])
            company_id = self._company(record["company"], existing.get("company_id")) if record.get("company") else None
            deal_id = self._deal(record["deal"], existing.get("deal_id")) if record.get("deal") else None
            note_id = self._note(record["note"], existing.get("note_id")) if record.get("note") else None

            if company_id:
                self._associate("contacts", contact_id, "companies", company_id)
            if deal_id:
                self._associate("deals", deal_id, "contacts", contact_id)
                if company_id:
                    self._associate("deals", deal_id, "companies", company_id)
            if note_id:
                self._associate("notes", note_id, "contacts", contact_id)
        except _NotFound as missing:
            raise CrmError(f"HubSpot couldn't find {missing}.") from None
        return {
            "contact_id": contact_id,
            "company_id": company_id,
            "deal_id": deal_id,
            "note_id": note_id,
            "contact_url": self._url("contacts", contact_id),
            "deal_url": self._url("deals", deal_id),
        }


def _simplify(text):
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def _match_stage(wanted, stages):
    """Finds `wanted` among a pipeline's stages by ID, then by label ("closedwon" matches "Closed won")."""
    for stage in stages:
        if stage.get("id") == wanted:
            return wanted
    for stage in stages:
        if _simplify(stage.get("label", "")) == _simplify(wanted):
            return stage.get("id")
    if _simplify(wanted) == "closedwon":
        # The label may have been renamed; HubSpot still marks the won stage as closed at 100%.
        for stage in stages:
            meta = stage.get("metadata") or {}
            if str(meta.get("isClosed")).lower() == "true" and str(meta.get("probability")) in ("1", "1.0"):
                return stage.get("id")
    return None


def _retry_after(response):
    try:
        return min(MAX_RETRY_WAIT_SECONDS, max(0.0, float(response.headers.get("Retry-After", 1))))
    except ValueError:
        return 1.0


def _error_message(response):
    try:
        return response.json().get("message") or f"status {response.status_code}"
    except ValueError:
        return f"status {response.status_code}"
