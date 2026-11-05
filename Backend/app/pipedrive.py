"""Creates or updates the Pipedrive records for one document: a person, an organization, a deal and a note."""

import html
import logging
import time

import httpx

from .crm import CrmError, _retry_after

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10


class _NotFound(Exception):
    pass


class Pipedrive:
    BASE = "https://api.pipedrive.com/v1"

    def __init__(self, token, domain=None, client=None, base_url=None):
        self.token = token
        # The company's subdomain, as in acme.pipedrive.com. Only used for links to the records.
        self.domain = domain
        self.client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
        self.base = (base_url or self.BASE).rstrip("/")

    def _request(self, method, path, body=None, params=None):
        headers = {"x-api-token": self.token, "Content-Type": "application/json"}
        for attempt in (1, 2):
            try:
                response = self.client.request(
                    method, self.base + path, json=body, params=params, headers=headers, timeout=TIMEOUT_SECONDS
                )
            except httpx.HTTPError as error:
                if attempt == 2:
                    raise CrmError("We couldn't reach Pipedrive. Please try again.", retryable=True) from error
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 2:
                    raise CrmError("Pipedrive is busy right now. Please try again in a minute.", retryable=True)
                time.sleep(_retry_after(response))
                continue
            if response.status_code in (401, 403):
                raise CrmError("Pipedrive rejected the API token. Check PIPEDRIVE_API_TOKEN.")
            if response.status_code in (404, 410):
                raise _NotFound(path)
            if response.status_code >= 400:
                message = _error_message(response)
                logger.warning("Pipedrive %s %s failed: %s", method, path, message)
                raise CrmError(f"Pipedrive didn't accept the record: {message}")
            return (response.json() if response.content else {}).get("data")

    def _save(self, kind, existing_id, body):
        # A record deleted in Pipedrive since the last send is simply made again.
        if existing_id:
            try:
                return self._request("PUT", f"/{kind}/{existing_id}", body)["id"]
            except _NotFound:
                pass
        return self._request("POST", f"/{kind}", body)["id"]

    def _find(self, kind, term, field):
        data = self._request(
            "GET", f"/{kind}/search", params={"term": term, "fields": field, "exact_match": "true", "limit": 1}
        )
        items = (data or {}).get("items") or []
        return items[0]["item"] if items else None

    def _person(self, contact, org_id, existing_id):
        name = " ".join(v for v in (contact.get("firstname"), contact.get("lastname")) if v) or contact["email"]
        body = {"name": name, "email": [{"value": contact["email"], "primary": True, "label": "work"}]}
        if contact.get("phone"):
            body["phone"] = [{"value": contact["phone"], "primary": True, "label": "work"}]
        if org_id:
            body["org_id"] = org_id
        if not existing_id:
            # Pipedrive has no upsert, so the email is looked up first, as HubSpot matches contacts by email.
            found = self._find("persons", contact["email"], "email")
            existing_id = found and found["id"]
        return self._save("persons", existing_id, body)

    def _organization(self, company, existing_id):
        name = company.get("name") or company.get("domain")
        body = {"name": name}
        if company.get("address"):
            body["address"] = company["address"]
        if not existing_id:
            found = self._find("organizations", name, "name")
            existing_id = found and found["id"]
        return self._save("organizations", existing_id, body)

    def _deal(self, deal, person_id, org_id, existing_id):
        body = {"title": deal["dealname"], "value": float(deal["amount"]), "person_id": person_id, "status": "won"}
        if deal.get("currency"):
            body["currency"] = deal["currency"]
        if org_id:
            body["org_id"] = org_id
        if deal.get("closedate"):
            body["expected_close_date"] = deal["closedate"]
        return self._save("deals", existing_id, body)

    def _note(self, content, person_id, org_id, deal_id, existing_id):
        # Notes are HTML in Pipedrive, so text from the document is escaped before line breaks are added.
        body = {"content": html.escape(content).replace("\n", "<br>"), "person_id": person_id}
        if org_id:
            body["org_id"] = org_id
        if deal_id:
            body["deal_id"] = deal_id
        return self._save("notes", existing_id, body)

    def _url(self, kind, record_id):
        if not self.domain or not record_id:
            return None
        return f"https://{self.domain}.pipedrive.com/{kind}/{record_id}"

    def push(self, record, existing=None):
        """Sends `record`, the same shape HubSpot gets. Passing the last result updates the same records."""
        existing = existing or {}
        contact = record["contact"]
        note = record.get("note") or ""
        if contact.get("jobtitle"):
            # Not every Pipedrive account has a job title field on people, so it goes in the note.
            note = f"Job title: {contact['jobtitle']}" + (f"\n\n{note}" if note else "")
        try:
            org_id = self._organization(record["company"], existing.get("company_id")) if record.get("company") else None
            person_id = self._person(contact, org_id, existing.get("contact_id"))
            deal_id = self._deal(record["deal"], person_id, org_id, existing.get("deal_id")) if record.get("deal") else None
            note_id = self._note(note, person_id, org_id, deal_id, existing.get("note_id")) if note else None
        except _NotFound as missing:
            raise CrmError(f"Pipedrive couldn't find {missing}.") from None
        return {
            "contact_id": str(person_id),
            "company_id": str(org_id) if org_id else None,
            "deal_id": str(deal_id) if deal_id else None,
            "note_id": str(note_id) if note_id else None,
            "contact_url": self._url("person", person_id),
            "deal_url": self._url("deal", deal_id),
        }

    def find_matches(self, record):
        """The records already in Pipedrive that sending `record` would touch."""
        matches = {"contact": None, "company": None, "deal": None}
        try:
            email = (record.get("contact") or {}).get("email")
            if email:
                found = self._find("persons", email, "email")
                if found:
                    matches["contact"] = self._match("person", found, found.get("name") or email)
            company = record.get("company")
            if company:
                name = company.get("name") or company.get("domain")
                found = self._find("organizations", name, "name")
                if found:
                    matches["company"] = self._match("organization", found, found.get("name") or name)
            deal = record.get("deal")
            if deal:
                found = self._find("deals", deal["dealname"], "title")
                if found:
                    matches["deal"] = {**self._match("deal", found, found.get("title") or deal["dealname"]), "amount": found.get("value")}
        except _NotFound as missing:
            raise CrmError(f"Pipedrive couldn't find {missing}.") from None
        return matches

    def _match(self, kind, found, name):
        # Search results don't say when a record last changed, so there's no date to show.
        return {"id": str(found["id"]), "name": name, "updated_at": None, "url": self._url(kind, found["id"])}


def _error_message(response):
    try:
        body = response.json()
        return body.get("error") or body.get("error_info") or f"status {response.status_code}"
    except ValueError:
        return f"status {response.status_code}"
