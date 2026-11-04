"""End-to-end check of the n8n path with nothing real involved.

Starts a throwaway Postgres, a fake HubSpot, the Inkfold API (pointed at n8n) and n8n itself
(with its own throwaway data folder), then sends a demo document through the workflow and checks
it comes back synced. Also checks that n8n turns away a request with a bad signature.

Run from the repo root, after n8n has been installed once by n8n/run-local.sh:
    Backend/.venv/bin/python n8n/test/e2e.py
"""

import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pgserver

ROOT = Path(__file__).resolve().parents[2]
API_PORT, N8N_PORT = 8021, 5691
API = f"http://127.0.0.1:{API_PORT}"
WEBHOOK = f"http://127.0.0.1:{N8N_PORT}/webhook/inkfold"
SECRET = secrets.token_hex(24)
# The fake HubSpot turns this contact away, to check that a failure comes back too.
REJECTED_EMAIL = "lina@cedarandco.example.com"

calls = []


class FakeHubSpot(BaseHTTPRequestHandler):
    next_id = 7001

    def _reply(self, body, status=200):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _record(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"null")
        calls.append((self.command, self.path, self.headers.get("Authorization"), body))
        return body

    def _new_id(self):
        FakeHubSpot.next_id += 1
        return str(FakeHubSpot.next_id)

    def do_POST(self):
        body = self._record()
        if self.path.endswith("/batch/upsert") and body["inputs"][0]["id"] == REJECTED_EMAIL:
            return self._reply({"message": "Authentication credentials not found."}, status=401)
        if self.path.endswith("/batch/upsert"):
            return self._reply({"results": [{"id": "6001"}]})
        if self.path.endswith("/search"):
            return self._reply({"total": 0, "results": []})
        self._reply({"id": self._new_id()})

    def do_GET(self):
        self._reply({})

    def do_PATCH(self):
        self._record()
        self._reply({"id": self.path.rsplit("/", 1)[1]})

    def do_PUT(self):
        self._record()
        self._reply({})

    def log_message(self, *args):
        pass


def wait_for(url, seconds, process, name):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if process.poll() is not None:
            sys.exit(f"{name} stopped while starting (exit {process.returncode}).")
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    sys.exit(f"{name} didn't start within {seconds}s.")


def free(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def check(condition, message):
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        raise SystemExit(1)


def main():
    for port in (API_PORT, N8N_PORT):
        if not free(port):
            sys.exit(f"Port {port} is in use.")
    work = Path(tempfile.mkdtemp(prefix="inkfold-e2e-"))
    processes = []
    hubspot = ThreadingHTTPServer(("127.0.0.1", 0), FakeHubSpot)
    threading.Thread(target=hubspot.serve_forever, daemon=True).start()
    hubspot_url = f"http://127.0.0.1:{hubspot.server_address[1]}"
    database = pgserver.get_server(work / "pg", cleanup_mode="stop")
    try:
        env = {
            **os.environ,
            "DATABASE_URL": database.get_uri(),
            "JWT_SECRET": secrets.token_hex(32),
            "GEMINI_API_KEY": "not-used-by-this-test",
            "N8N_WEBHOOK_URL": WEBHOOK,
            "N8N_SHARED_SECRET": SECRET,
            "PUBLIC_API_URL": API,
        }
        backend = ROOT / "Backend"
        subprocess.run([backend / ".venv/bin/python", "scripts/init_db.py"], cwd=backend, env=env, check=True)
        api = subprocess.Popen(
            [backend / ".venv/bin/uvicorn", "asgi:app", "--port", str(API_PORT)],
            cwd=backend, env=env, stdout=open(work / "api.log", "w"), stderr=subprocess.STDOUT, start_new_session=True,
        )
        processes.append(api)

        n8n_env = {
            **os.environ,
            "N8N_USER_FOLDER": str(work / "n8n"),
            "N8N_PORT": str(N8N_PORT),
            "INKFOLD_SHARED_SECRET": SECRET,
            "HUBSPOT_TOKEN": "fake-token",
            "HUBSPOT_PORTAL_ID": "42",
            "HUBSPOT_BASE_URL": hubspot_url,
        }
        n8n = subprocess.Popen(
            ["bash", ROOT / "n8n/run-local.sh"], env=n8n_env,
            stdout=open(work / "n8n.log", "w"), stderr=subprocess.STDOUT, start_new_session=True,
        )
        processes.append(n8n)
        wait_for(f"{API}/api/health", 30, api, "The API")
        wait_for(f"http://127.0.0.1:{N8N_PORT}/healthz", 180, n8n, "n8n")

        # n8n reports healthy a little before it registers the webhook, so wait for that too.
        deadline = time.time() + 60
        while True:
            bad = httpx.post(WEBHOOK, content=b'{"document_id":"x"}', headers={
                "Content-Type": "application/json",
                "X-Inkfold-Timestamp": str(int(time.time())),
                "X-Inkfold-Signature": "sha256=" + "0" * 64,
            })
            if bad.status_code != 404 or time.time() > deadline:
                break
            time.sleep(1)
        check(bad.status_code == 401, f"n8n turns away a bad signature ({bad.status_code})")
        check(not calls, "nothing reached HubSpot for it")

        token = httpx.post(f"{API}/api/auth/demo").json()["token"]
        auth = {"Authorization": f"Bearer {token}"}
        documents = httpx.get(f"{API}/api/documents", headers=auth).json()["documents"]
        invoice = next(d for d in documents if d["filename"] == "invoice.pdf")
        sent = httpx.post(f"{API}/api/documents/{invoice['id']}/sync", headers=auth)
        check(sent.status_code == 200 and sent.json()["status"] == "syncing", f"the API hands the invoice to n8n ({sent.status_code})")

        deadline = time.time() + 30
        while time.time() < deadline:
            document = httpx.get(f"{API}/api/documents/{invoice['id']}", headers=auth).json()
            if document["status"] != "syncing":
                break
            time.sleep(0.5)
        check(document["status"] == "synced", f"n8n's signed callback marks it synced ({document['status']}: {document.get('error')})")
        crm = document["crm"]
        check(crm["contact_id"] == "6001" and crm["deal_id"] and crm["company_id"] and crm["note_id"], f"the HubSpot ids came back {crm}")
        check(crm["contact_url"] == "https://app.hubspot.com/contacts/42/record/0-1/6001", "the contact link is right")
        check(all(auth_header == "Bearer fake-token" for _, _, auth_header, _ in calls), "every HubSpot call used the token")
        deal = next(body for method, path, _, body in calls if method == "POST" and path == "/crm/v3/objects/deals")
        check(deal["properties"]["amount"] == "1249.19" and deal["properties"]["deal_currency_code"] == "USD", "the deal has the invoice total")
        links = [path for method, path, _, _ in calls if method == "PUT"]
        check(len(links) == 4, f"contact, company, deal and note are linked ({len(links)} links)")
        events = [e["detail"] for e in document["sync_events"]]
        check(any(e.startswith("Saved in HubSpot") for e in events), "the history shows the save")

        lead = next(d for d in documents if d["filename"] == "lead-form.pdf")
        sent = httpx.post(f"{API}/api/documents/{lead['id']}/sync", headers=auth)
        check(sent.status_code == 200, f"the lead form is handed to n8n ({sent.status_code})")
        deadline = time.time() + 30
        while time.time() < deadline:
            document = httpx.get(f"{API}/api/documents/{lead['id']}", headers=auth).json()
            if document["status"] != "syncing":
                break
            time.sleep(0.5)
        check(document["status"] == "sync_failed", f"a HubSpot rejection comes back as failed ({document['status']})")
        check(document["error"] == "HubSpot rejected the access token. Check HUBSPOT_TOKEN.", f"with HubSpot's reason ({document['error']})")
        print("All checks passed.")
    except SystemExit:
        for name in ("api.log", "n8n.log"):
            log = work / name
            if log.exists():
                print(f"--- last lines of {name} ---")
                print("\n".join(log.read_text().splitlines()[-25:]))
        raise
    finally:
        for process in processes:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for process in processes:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        hubspot.shutdown()
        database.cleanup()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
