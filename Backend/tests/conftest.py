import json
from types import SimpleNamespace

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.store import Store

N8N_URL = "http://n8n.test/webhook/inkfold"
SECRET = "test-shared-secret"
EXTRACTION = {
    "is_business_document": True,
    "doc_type": "business_card",
    "fields": [
        {"key": "contact.first_name", "value": "Omar", "source": "Omar Siddiqui", "confidence": 0.97},
        {"key": "contact.last_name", "value": "Siddiqui", "source": "Omar Siddiqui", "confidence": 0.97},
        {"key": "contact.email", "value": "omar@kestrel.example", "source": "omar@kestrel.example", "confidence": 0.96},
        {"key": "company.name", "value": "Kestrel Logistics", "source": "KESTREL LOGISTICS", "confidence": 0.5},
    ],
    "line_items": [],
}


@pytest.fixture(scope="session")
def database_url(tmp_path_factory):
    import pgserver

    server = pgserver.get_server(tmp_path_factory.mktemp("pg"), cleanup_mode="delete")
    yield server.get_uri()
    server.cleanup()


@pytest.fixture
def store(database_url):
    store = Store(database_url)
    store.init_schema()
    with psycopg.connect(database_url, autocommit=True) as db:
        db.execute("TRUNCATE users, documents, sync_events, rate_limits, password_reset_tokens CASCADE")
    yield store
    store.close()


class FakeGemini:
    """Plays the Gemini SDK. Queue replies (dicts) or exceptions; the default reply is a business card."""

    def __init__(self):
        self.replies = []
        self.calls = 0
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls += 1
        reply = self.replies.pop(0) if self.replies else EXTRACTION
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(text=json.dumps(reply))


class FakeServices:
    """One transport for every outside call: HubSpot's REST API and the n8n webhook."""

    def __init__(self):
        self.requests = []
        self.n8n_status = 200
        self.hubspot_status = None
        self.next_id = 500

    def __call__(self, request):
        body = json.loads(request.content) if request.content else None
        self.requests.append(SimpleNamespace(method=request.method, url=str(request.url), path=request.url.path, body=body, headers=request.headers, content=request.content))
        if str(request.url) == N8N_URL:
            return httpx.Response(self.n8n_status, json={"message": "Workflow was started"})
        if self.hubspot_status:
            return httpx.Response(self.hubspot_status, json={"message": "Nope"})
        if request.url.path.endswith("/batch/upsert"):
            return httpx.Response(200, json={"results": [{"id": "900"}]})
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"total": 0, "results": []})
        if request.method in ("POST", "PATCH") and "/crm/v3/" in request.url.path:
            self.next_id += 1
            return httpx.Response(200, json={"id": request.url.path.rsplit("/", 1)[1] if request.method == "PATCH" else str(self.next_id)})
        return httpx.Response(200, json={})

    def hubspot_calls(self):
        return [(r.method, r.path) for r in self.requests if r.url != N8N_URL]

    def n8n_calls(self):
        return [r for r in self.requests if r.url == N8N_URL]


def make_settings(database_url, **overrides):
    settings = {
        "database_url": database_url,
        "jwt_secret": "test-jwt-secret-that-is-long-enough-for-hs256",
        "gemini_api_key": "unused",
        "extract_models": ["fake-model"],
        "hubspot_token": "pat-test",
        "hubspot_portal_id": "4242",
        "hubspot_deal_stage": "closedwon",
        "hubspot_base_url": "",
        "n8n_webhook_url": "",
        "n8n_shared_secret": "",
        "public_api_url": "http://api.test",
        "cors_origins": ["http://localhost:5180"],
        "app_url": "http://localhost:5180",
        "smtp": None,
        "uploads_per_user_day": 10,
        "uploads_per_day": 100,
        "behind_proxy": False,
    }
    settings.update(overrides)
    return settings


@pytest.fixture
def gemini():
    return FakeGemini()


@pytest.fixture
def services():
    return FakeServices()


class FakeMailer:
    """Keeps the emails instead of sending them. Set `error` to make sending fail."""

    def __init__(self):
        self.sent = []
        self.error = None

    def __call__(self, to, subject, heading, text, action_label, action_url, note_lead, note):
        if self.error:
            raise self.error
        self.sent.append(SimpleNamespace(to=to, subject=subject, text=text, url=action_url))


@pytest.fixture
def mailer():
    return FakeMailer()


@pytest.fixture
def build(store, gemini, services, database_url):
    """Builds a TestClient. Pass setting overrides, such as n8n_webhook_url, and send_email."""

    def _build(send_email=None, **overrides):
        app = create_app(
            make_settings(database_url, **overrides),
            store=store,
            gemini_client=gemini,
            http_client=httpx.Client(transport=httpx.MockTransport(services)),
            send_email=send_email,
        )
        return TestClient(app)

    return _build


@pytest.fixture
def client(build):
    return build()


def sign_in(client, email="sara@example.com", password="correct-horse-battery"):
    response = client.post("/api/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    return {"Authorization": "Bearer " + response.json()["token"]}


@pytest.fixture
def auth(client):
    return sign_in(client)
