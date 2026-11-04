"""Hands a record to n8n and checks the reply n8n sends back. Both directions are signed."""

import hashlib
import hmac
import json
import time
from typing import Literal

import httpx
from pydantic import BaseModel, ValidationError

SIGNATURE_HEADER = "X-Inkfold-Signature"
TIMESTAMP_HEADER = "X-Inkfold-Timestamp"
SIGNATURE_PREFIX = "sha256="
# An old request can't be replayed later, even if it was signed correctly.
TOLERANCE_SECONDS = 300
TIMEOUT_SECONDS = 10


class SyncError(Exception):
    pass


class CallbackResult(BaseModel):
    document_id: str
    status: Literal["synced", "failed"]
    crm: dict = {}
    detail: str = ""


def sign(secret, timestamp, body):
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def verify_signature(secret, timestamp, signature_header, body, now=None, tolerance=TOLERANCE_SECONDS):
    if not secret or not timestamp or not signature_header or not signature_header.startswith(SIGNATURE_PREFIX):
        return False
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - sent_at) > tolerance:
        return False
    expected = sign(secret, timestamp, body)
    return hmac.compare_digest(expected, signature_header[len(SIGNATURE_PREFIX):])


def signed_headers(secret, body, now=None):
    timestamp = str(int(now if now is not None else time.time()))
    return {
        "Content-Type": "application/json",
        TIMESTAMP_HEADER: timestamp,
        SIGNATURE_HEADER: SIGNATURE_PREFIX + sign(secret, timestamp, body),
    }


def build_n8n_payload(document_id, record, callback_url, existing=None):
    return {"document_id": document_id, "record": record, "existing": existing or {}, "callback_url": callback_url}


def send_to_n8n(url, secret, payload, client=None):
    body = json.dumps(payload, separators=(",", ":")).encode()
    client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        response = client.post(url, content=body, headers=signed_headers(secret, body), timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError as error:
        raise SyncError("We couldn't reach the n8n workflow. Check that n8n is running.") from error
    if not response.is_success:
        raise SyncError(f"The n8n workflow didn't accept the record (status {response.status_code}).")


def parse_callback(body):
    try:
        return CallbackResult.model_validate(json.loads(body))
    except (ValueError, ValidationError) as error:
        raise ValueError("Invalid callback body.") from error
