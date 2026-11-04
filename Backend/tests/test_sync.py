import json

import httpx
import pytest

from app.sync import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    SyncError,
    build_n8n_payload,
    parse_callback,
    send_to_n8n,
    sign,
    signed_headers,
    verify_signature,
)

SECRET = "shared-secret"
BODY = b'{"document_id":"d1"}'


def test_sign_matches_a_known_value():
    # Computed independently: HMAC-SHA256("shared-secret", "1700000000.{body}")
    import hashlib
    import hmac

    expected = hmac.new(b"shared-secret", b"1700000000." + BODY, hashlib.sha256).hexdigest()
    assert sign(SECRET, "1700000000", BODY) == expected


def test_valid_signature():
    headers = signed_headers(SECRET, BODY, now=1700000000)
    assert verify_signature(SECRET, headers[TIMESTAMP_HEADER], headers[SIGNATURE_HEADER], BODY, now=1700000100)


@pytest.mark.parametrize(
    "secret, body, now",
    [("wrong", BODY, 1700000000), (SECRET, BODY + b" ", 1700000000), (SECRET, BODY, 1700000301), (SECRET, BODY, 1699999000)],
)
def test_wrong_secret_changed_body_stale_or_future(secret, body, now):
    headers = signed_headers(SECRET, BODY, now=1700000000)
    assert not verify_signature(secret, headers[TIMESTAMP_HEADER], headers[SIGNATURE_HEADER], body, now=now)


@pytest.mark.parametrize("timestamp, signature", [(None, "sha256=x"), ("abc", "sha256=x"), ("1700000000", None), ("1700000000", "md5=x")])
def test_malformed_headers(timestamp, signature):
    assert not verify_signature(SECRET, timestamp, signature, BODY, now=1700000000)


def test_no_secret_never_verifies():
    headers = signed_headers("", BODY, now=1700000000)
    assert not verify_signature("", headers[TIMESTAMP_HEADER], headers[SIGNATURE_HEADER], BODY, now=1700000000)


def test_payload_shape():
    assert build_n8n_payload("d1", {"contact": {}}, "https://api.example/api/sync/callback") == {
        "document_id": "d1",
        "record": {"contact": {}},
        "existing": {},
        "callback_url": "https://api.example/api/sync/callback",
    }


def test_send_signs_the_exact_bytes_it_sends():
    seen = {}

    def n8n(request):
        seen["body"] = request.content
        seen["headers"] = request.headers
        return httpx.Response(200, json={"message": "Workflow was started"})

    send_to_n8n("http://n8n.local/webhook/inkfold", SECRET, {"document_id": "d1", "record": {"a": "é"}}, client=httpx.Client(transport=httpx.MockTransport(n8n)))
    assert json.loads(seen["body"]) == {"document_id": "d1", "record": {"a": "é"}}
    assert verify_signature(SECRET, seen["headers"][TIMESTAMP_HEADER], seen["headers"][SIGNATURE_HEADER], seen["body"])


def test_send_failures():
    refused = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(SyncError, match="status 404"):
        send_to_n8n("http://n8n.local/x", SECRET, {}, client=refused)

    def down(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(SyncError, match="Check that n8n is running"):
        send_to_n8n("http://n8n.local/x", SECRET, {}, client=httpx.Client(transport=httpx.MockTransport(down)))


def test_parse_callback():
    result = parse_callback(b'{"document_id": "d1", "status": "synced", "crm": {"contact_id": "5"}}')
    assert result.status == "synced" and result.crm == {"contact_id": "5"} and result.detail == ""
    for bad in (b"nope", b'{"document_id": "d1", "status": "done"}', b"{}"):
        with pytest.raises(ValueError):
            parse_callback(bad)
