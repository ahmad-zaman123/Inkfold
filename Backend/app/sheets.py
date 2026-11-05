"""Writes one row per document to a Google Sheet, for teams that keep their leads or expenses in a spreadsheet.

It signs in as a Google service account. Share the sheet with the account's email address as an editor.
"""

import json
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import quote

import httpx
import jwt

from .crm import CrmError, _retry_after

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10
SCOPE = "https://www.googleapis.com/auth/spreadsheets"
TOKEN_URL = "https://oauth2.googleapis.com/token"
# The first column holds the document's id, so sending again finds and updates the same row,
# even after someone sorts the sheet or deletes rows above it.
COLUMNS = [
    ("Inkfold ID", None),
    ("Sent at", None),
    ("Type", None),
    ("File", None),
    ("First name", "contact.first_name"),
    ("Last name", "contact.last_name"),
    ("Email", "contact.email"),
    ("Phone", "contact.phone"),
    ("Job title", "contact.job_title"),
    ("Company", "company.name"),
    ("Website", "company.domain"),
    ("Address", "company.address"),
    ("Number", "invoice.number"),
    ("Date", "invoice.issue_date"),
    ("Due or delivery date", "invoice.due_date"),
    ("Currency", "invoice.currency"),
    ("Total", "invoice.total"),
    ("Paid with", "invoice.payment_method"),
    ("Line items", "invoice.line_items"),
    ("Location", "contact.location"),
    ("LinkedIn", "contact.linkedin"),
    ("Skills", "resume.skills"),
    ("Notes", "notes"),
]
DOC_TYPE_NAMES = {
    "invoice": "Invoice", "receipt": "Receipt", "purchase_order": "Purchase order", "business_card": "Business card",
    "lead_form": "Lead form", "resume": "Resume", "other": "Other",
}


class _BadRange(Exception):
    pass


def _column_letter(index):
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


LAST_COLUMN = _column_letter(len(COLUMNS) - 1)


def _cell(key, fields):
    value = (fields.get(key) or {}).get("value")
    if value is None:
        return ""
    if key == "invoice.line_items":
        return "; ".join(
            item["description"] + (f" x {item['quantity']}" if item.get("quantity") else "")
            + (f" = {item['amount']}" if item.get("amount") else "")
            for item in value
        )
    if key == "invoice.total" and not fields[key].get("issues"):
        # A number, so the sheet can add the column up.
        return float(Decimal(value))
    return str(value)


def row_for(document, fields, sent_at=None):
    sent_at = sent_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    meta = [str(document["id"]), sent_at, DOC_TYPE_NAMES.get(document["doc_type"], document["doc_type"]), document["filename"]]
    return meta + [_cell(key, fields) for _, key in COLUMNS[len(meta):]]


class GoogleSheets:
    BASE = "https://sheets.googleapis.com/v4/spreadsheets"

    def __init__(self, service_account, spreadsheet_id, tab="Inkfold", client=None, base_url=None, token_url=None):
        """`service_account` is the key file's JSON, as text or already parsed."""
        info = json.loads(service_account) if isinstance(service_account, str) else service_account
        self.email = info["client_email"]
        # A key pasted into a dashboard sometimes ends up with "\\n" where the file had line breaks.
        key = info["private_key"]
        self.private_key = key.replace("\\n", "\n") if "\n" not in key.strip() else key
        self.token_url = token_url or info.get("token_uri") or TOKEN_URL
        self.spreadsheet_id = spreadsheet_id
        self.tab = tab
        self.client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
        self.base = (base_url or self.BASE).rstrip("/")
        self._token = None
        self._token_expires = 0.0

    def _access_token(self):
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        now = int(time.time())
        try:
            assertion = jwt.encode(
                {"iss": self.email, "scope": SCOPE, "aud": self.token_url, "iat": now, "exp": now + 3600},
                self.private_key,
                algorithm="RS256",
            )
        except (jwt.PyJWTError, ValueError, TypeError):
            raise CrmError("The private key in GOOGLE_SERVICE_ACCOUNT_JSON can't be read. Paste the whole JSON file again.") from None
        try:
            response = self.client.post(
                self.token_url,
                data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
                timeout=TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as error:
            raise CrmError("We couldn't reach Google. Please try again.", retryable=True) from error
        if response.status_code != 200:
            logger.warning("Google sign-in failed: %s", response.text[:300])
            raise CrmError("Google refused the service account. Check GOOGLE_SERVICE_ACCOUNT_JSON.")
        body = response.json()
        self._token = body["access_token"]
        self._token_expires = time.time() + float(body.get("expires_in", 3600))
        return self._token

    def _request(self, method, path, body=None, params=None):
        url = f"{self.base}/{self.spreadsheet_id}{path}"
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self._access_token()}"}
            try:
                response = self.client.request(method, url, json=body, params=params, headers=headers, timeout=TIMEOUT_SECONDS)
            except httpx.HTTPError as error:
                if attempt == 2:
                    raise CrmError("We couldn't reach Google Sheets. Please try again.", retryable=True) from error
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 2:
                    raise CrmError("Google Sheets is busy right now. Please try again in a minute.", retryable=True)
                time.sleep(_retry_after(response))
                continue
            if response.status_code == 401 and attempt == 1:
                self._token = None
                continue
            if response.status_code in (401, 403):
                raise CrmError(f"The service account can't edit this sheet. Share the sheet with {self.email} as an editor.")
            if response.status_code == 404:
                raise CrmError("Google couldn't find the sheet. Check GOOGLE_SHEETS_ID.")
            if response.status_code == 400 and "Unable to parse range" in response.text:
                raise _BadRange()
            if response.status_code >= 400:
                logger.warning("Google Sheets %s %s failed: %s", method, path, response.text[:300])
                raise CrmError("Google Sheets didn't accept the row.")
            return response.json() if response.content else {}

    def _range(self, cells):
        return "/values/" + quote(f"'{self.tab}'!{cells}", safe="")

    def _ids(self):
        """The first column, making the tab and its header row if they aren't there yet."""
        try:
            values = self._request("GET", self._range("A:A")).get("values") or []
        except _BadRange:
            self._request("POST", ":batchUpdate", {"requests": [{"addSheet": {"properties": {"title": self.tab}}}]})
            values = []
        if not values:
            self._request(
                "PUT", self._range(f"A1:{LAST_COLUMN}1"), {"values": [[name for name, _ in COLUMNS]]},
                params={"valueInputOption": "RAW"},
            )
            values = [[COLUMNS[0][0]]]
        return [row[0] if row else "" for row in values]

    def push(self, document, fields):
        row = row_for(document, fields)
        ids = self._ids()
        # RAW keeps a value such as "=HYPERLINK(...)" as text, so nothing in a document runs as a formula.
        if str(document["id"]) in ids:
            number = ids.index(str(document["id"])) + 1
            self._request(
                "PUT", self._range(f"A{number}:{LAST_COLUMN}{number}"), {"values": [row]}, params={"valueInputOption": "RAW"}
            )
        else:
            result = self._request(
                "POST", self._range(f"A1:{LAST_COLUMN}1") + ":append", {"values": [row]},
                params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
            )
            updated = (result.get("updates") or {}).get("updatedRange", "")
            number = int("".join(ch for ch in updated.rsplit("!", 1)[-1].split(":")[0] if ch.isdigit()) or 0) or len(ids) + 1
        return {"row": number, "sheet_url": f"https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}/edit"}
