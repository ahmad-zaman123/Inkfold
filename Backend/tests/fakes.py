"""Pipedrive and Google Sheets in memory, for tests. Each is an httpx transport handler."""

import json
import re
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote

import httpx


class FakePipedrive:
    def __init__(self):
        self.records = {"persons": {}, "organizations": {}, "deals": {}, "notes": {}}
        self.next_id = 100
        self.requests = []
        self.status = None

    def __call__(self, request):
        body = json.loads(request.content) if request.content else None
        path = request.url.path.removeprefix("/v1")
        self.requests.append(SimpleNamespace(method=request.method, path=path, body=body, headers=request.headers, params=dict(request.url.params)))
        if self.status:
            return httpx.Response(self.status, json={"success": False, "error": "Nope"})
        kind, *rest = path.strip("/").split("/")
        table = self.records[kind]
        if rest == ["search"]:
            term, field = request.url.params["term"], request.url.params["fields"]
            items = [{"item": record} for record in table.values() if term in self._values(record, field)]
            return httpx.Response(200, json={"success": True, "data": {"items": items[:1]}})
        if request.method == "POST":
            self.next_id += 1
            table[self.next_id] = {**body, "id": self.next_id}
            return httpx.Response(201, json={"success": True, "data": table[self.next_id]})
        if request.method == "PUT":
            record_id = int(rest[0])
            if record_id not in table:
                return httpx.Response(404, json={"success": False, "error": "Not found"})
            table[record_id].update(body)
            return httpx.Response(200, json={"success": True, "data": table[record_id]})
        return httpx.Response(400, json={"success": False, "error": "Unexpected"})

    @staticmethod
    def _values(record, field):
        value = record.get(field)
        if isinstance(value, list):
            return [entry["value"] for entry in value]
        return [value]


class FakeSheets:
    """One spreadsheet. `rows` is the tab's contents; `tab` is None until the tab exists."""

    def __init__(self, tab=None):
        self.tab = tab
        self.rows = []
        self.tokens = 0
        self.requests = []
        self.status = None

    def __call__(self, request):
        if request.url.host == "oauth2.googleapis.com":
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"] and form["assertion"]
            self.tokens += 1
            return httpx.Response(200, json={"access_token": f"token-{self.tokens}", "expires_in": 3600})
        assert request.headers["authorization"].startswith("Bearer token-")
        body = json.loads(request.content) if request.content else None
        path = unquote(request.url.path)
        self.requests.append(SimpleNamespace(method=request.method, path=path, body=body, params=dict(request.url.params)))
        if self.status:
            return httpx.Response(self.status, json={"error": {"message": "Nope"}})
        if path.endswith(":batchUpdate"):
            self.tab = body["requests"][0]["addSheet"]["properties"]["title"]
            return httpx.Response(200, json={})
        match = re.search(r"/values/'(.+)'!([A-Z]+)(\d*)(?::([A-Z]+)(\d*))?(:append)?$", path)
        tab, start_row, append = match.group(1), match.group(3), match.group(6)
        if tab != self.tab:
            return httpx.Response(400, json={"error": {"message": f"Unable to parse range: '{tab}'!A:A"}})
        if request.method == "GET":
            return httpx.Response(200, json={"values": [[row[0]] for row in self.rows]} if self.rows else {})
        if append:
            self.rows.append(body["values"][0])
            n = len(self.rows)
            return httpx.Response(200, json={"updates": {"updatedRange": f"'{tab}'!A{n}:W{n}"}})
        index = int(start_row) - 1
        while len(self.rows) <= index:
            self.rows.append([])
        self.rows[index] = body["values"][0]
        return httpx.Response(200, json={})


def service_account():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return json.dumps({"type": "service_account", "client_email": "inkfold@demo.iam.gserviceaccount.com",
                       "private_key": pem.decode(), "token_uri": "https://oauth2.googleapis.com/token"})
