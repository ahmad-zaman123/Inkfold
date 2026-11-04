import hashlib
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

import httpx
import jwt
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import AfterValidator, BaseModel, EmailStr, Field

from . import demo_data
from .config import load_settings
from .crm import CrmError, HubSpot
from .extract import ExtractionFailed, ExtractionUnavailable, extract_document
from .normalize import apply_edits, missing_for_sync, normalize_fields, to_crm_record
from .schema import FIELDS
from .store import SYNC_STALE_AFTER, Store
from .sync import SIGNATURE_HEADER, TIMESTAMP_HEADER, SyncError, build_n8n_payload, parse_callback, send_to_n8n, verify_signature

logger = logging.getLogger(__name__)

TOKEN_LIFETIME = timedelta(hours=8)
# Vercel refuses request bodies over 4.5 MB, so the limit sits just under it.
MAX_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_FIELD_LENGTH = 2000
BUSY = "Too many requests. Please try again in a little while."
AI_BUSY = "The AI service is busy right now. Please try again in a little while."
WRONG_TYPE = "Upload a PDF, PNG, JPG or TXT file."
NEEDS_EMAIL = "Add a valid email before sending to HubSpot."
# Name: (allowed hits, window, message). Kept in Postgres because serverless instances share no memory.
RATE_LIMITS = {
    "login": (20, timedelta(minutes=15), "Too many sign-in attempts. Please try again in a few minutes."),
    "register": (10, timedelta(hours=1), BUSY),
    "demo": (10, timedelta(hours=1), BUSY),
    "demo_all": (500, timedelta(days=1), "The demo is full for today. Please try again tomorrow."),
    "upload_user": (None, timedelta(days=1), "You've reached today's upload limit. Please try again tomorrow."),
    "upload_all": (None, timedelta(days=1), "Uploads are full for today. Please try again tomorrow."),
    "sync_user": (20, timedelta(days=1), "You've sent a lot to HubSpot today. Please try again tomorrow."),
}
SIGNATURES = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


def storable(value):
    # Postgres can't hold NUL characters, and lone surrogates can't be encoded at all.
    if any(c == "\x00" or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError("contains characters that can't be stored")
    return value


StorableText = Annotated[str, AfterValidator(storable)]


class Credentials(BaseModel):
    email: EmailStr
    password: StorableText = Field(min_length=1, max_length=200)


class Registration(BaseModel):
    email: EmailStr
    password: StorableText = Field(min_length=8, max_length=200)


class FieldEdits(BaseModel):
    fields: dict[str, Any] = Field(max_length=len(FIELDS))


def sniff_mime(data, filename):
    """Decides the type from the file's first bytes, never from what the browser claims."""
    for signature, mime in SIGNATURES:
        if data.startswith(signature):
            return mime
    if filename.lower().endswith(".txt") and b"\x00" not in data:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return "text/plain"
    return None


def clean_filename(name):
    name = re.sub(r"[\x00-\x1f\x7f]", "", os.path.basename(name or "")).strip()
    return storable(name[:200]) if name else "document"


def check_edit(key, value):
    if key not in FIELDS:
        raise HTTPException(status_code=400, detail=f"Unknown field: {key}")
    if FIELDS[key]["type"] == "line_items":
        if value is None:
            return
        if not isinstance(value, list) or len(value) > 100 or not all(isinstance(item, dict) for item in value):
            raise HTTPException(status_code=400, detail="Line items must be a list.")
        for item in value:
            for part in item.values():
                if part is not None and (not isinstance(part, (str, int, float)) or len(str(part)) > MAX_FIELD_LENGTH):
                    raise HTTPException(status_code=400, detail="Line items must hold short text or numbers.")
                if isinstance(part, str):
                    storable(part)
        return
    if value is not None and (not isinstance(value, str) or len(value) > MAX_FIELD_LENGTH):
        raise HTTPException(status_code=400, detail=f"{FIELDS[key]['label']} must be text up to {MAX_FIELD_LENGTH} characters.")
    if isinstance(value, str):
        try:
            storable(value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"{FIELDS[key]['label']} contains characters that can't be stored.")


def title_of(document):
    fields = document["fields"]
    name = " ".join(v for v in (fields["contact.first_name"]["value"], fields["contact.last_name"]["value"]) if v)
    return name or fields["company.name"]["value"] or document["filename"]


def is_stale_sync(document):
    return document["status"] == "syncing" and document["updated_at"] < datetime.now(timezone.utc) - SYNC_STALE_AFTER


def public_document(document, events=None):
    summary = {
        key: document[key]
        for key in ("id", "filename", "mime", "size", "doc_type", "status", "error", "created_at", "updated_at")
    }
    summary["id"] = str(document["id"])
    summary["title"] = title_of(document)
    if events is None:
        return summary
    missing = missing_for_sync(document["fields"])
    ready = document["status"] in ("needs_review", "sync_failed") or is_stale_sync(document)
    return {
        **summary,
        "fields": document["fields"],
        "crm": document["crm"],
        "missing": missing,
        "can_sync": ready and not missing,
        "sync_events": [{**event, "id": str(event["id"])} for event in events],
    }


def crm_summary(crm):
    parts = [f"contact {crm['contact_id']}"] if crm.get("contact_id") else []
    parts += [f"{name} {crm[key]}" for key, name in (("company_id", "company"), ("deal_id", "deal"), ("note_id", "note")) if crm.get(key)]
    return "Saved in HubSpot: " + ", ".join(parts) + "." if parts else "Saved in HubSpot."


def parse_id(value):
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=404, detail="Document not found.")


def create_app(settings=None, *, store=None, gemini_client=None, http_client=None):
    settings = settings or load_settings()
    store = store or Store(settings["database_url"])
    jwt_secret = settings["jwt_secret"]
    rate_limits = dict(RATE_LIMITS)
    rate_limits["upload_user"] = (settings["uploads_per_user_day"], *RATE_LIMITS["upload_user"][1:])
    rate_limits["upload_all"] = (settings["uploads_per_day"], *RATE_LIMITS["upload_all"][1:])
    http = http_client or httpx.Client()

    app = FastAPI(title="Inkfold", docs_url="/api/docs", openapi_url="/api/openapi.json")
    # Sessions use a bearer token rather than cookies, so credentials stay off.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings["cors_origins"],
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["Retry-After"],
    )

    @app.exception_handler(RequestValidationError)
    def invalid_request(request, error):
        # FastAPI's default repeats the input, which can be a password or text that can't be encoded.
        detail = [{"type": e["type"], "loc": e["loc"], "msg": e["msg"]} for e in error.errors()]
        return JSONResponse(status_code=422, content={"detail": detail})

    def client_ip(request):
        if settings["behind_proxy"] and request.headers.get("x-real-ip"):
            return request.headers["x-real-ip"]
        return request.client.host if request.client else "unknown"

    def enforce_limit(name, subject):
        limit, window, message = rate_limits[name]
        wait = store.hit_rate_limit(f"{name}:{subject}", limit, window)
        if wait is not None:
            raise HTTPException(status_code=429, detail=message, headers={"Retry-After": str(wait)})

    def session_for(user):
        now = datetime.now(timezone.utc)
        token = jwt.encode({"sub": str(user["id"]), "iat": now, "exp": now + TOKEN_LIFETIME}, jwt_secret, algorithm="HS256")
        return {"token": token, "user": public_user(user)}

    def public_user(user):
        return {"id": str(user["id"]), "email": user["email"], "is_demo": user["is_demo"]}

    def current_user(authorization: str = Header(default="")):
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(status_code=401, detail="Not signed in")
        try:
            claims = jwt.decode(token, jwt_secret, algorithms=["HS256"])
            user = store.get_user(uuid.UUID(claims["sub"]))
        except (jwt.InvalidTokenError, KeyError, ValueError):
            raise HTTPException(status_code=401, detail="Not signed in")
        if user is None:
            raise HTTPException(status_code=401, detail="Not signed in")
        return user

    def own_document(document_id, user):
        document = store.get_document(parse_id(document_id), user["id"])
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        return document

    def full(document):
        return public_document(document, store.sync_events(document["id"]))

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.post("/api/auth/register", status_code=201)
    def register(body: Registration, request: Request):
        enforce_limit("register", client_ip(request))
        user = store.create_user(body.email, body.password)
        if user is None:
            raise HTTPException(status_code=409, detail="An account with this email already exists.")
        return session_for(user)

    @app.post("/api/auth/login")
    def login(body: Credentials, request: Request):
        enforce_limit("login", client_ip(request))
        user = store.authenticate(body.email, body.password)
        if user is None:
            raise HTTPException(status_code=401, detail="Email or password is wrong.")
        return session_for(user)

    @app.post("/api/auth/demo")
    def demo(request: Request):
        enforce_limit("demo", client_ip(request))
        enforce_limit("demo_all", "all")
        documents = [
            {**document, "status": "needs_review", "fields": normalize_fields(document["fields"])}
            for document in demo_data.DEMO_DOCUMENTS
        ]
        return session_for(store.create_demo_user(documents))

    @app.get("/api/me")
    def me(user=Depends(current_user)):
        return public_user(user)

    @app.post("/api/documents", status_code=201)
    def upload(response: Response, file: UploadFile = File(...), user=Depends(current_user)):
        data = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Files can be up to 4 MB.")
        if not data:
            raise HTTPException(status_code=400, detail="This file is empty.")
        try:
            filename = clean_filename(file.filename)
        except ValueError:
            filename = "document"
        mime = sniff_mime(data, filename)
        if mime is None:
            raise HTTPException(status_code=415, detail=WRONG_TYPE)

        sha256 = hashlib.sha256(data).hexdigest()
        existing = store.find_document_by_hash(user["id"], sha256)
        if existing:
            response.status_code = 200
            return {**full(existing), "duplicate": True}

        # Counted before the AI call, because the call is what each limit protects.
        enforce_limit("upload_user", user["id"])
        enforce_limit("upload_all", "all")
        document = {"filename": filename, "mime": mime, "size": len(data), "sha256": sha256}
        try:
            extracted = extract_document(data, mime, settings, client=gemini_client)
        except ExtractionUnavailable:
            raise HTTPException(status_code=503, detail=AI_BUSY)
        except ExtractionFailed as failure:
            # Kept with empty fields, so the user can still type the details in by hand.
            document.update(doc_type="other", status="failed", error=str(failure), fields=normalize_fields({}))
        else:
            document.update(doc_type=extracted["doc_type"], status="needs_review", fields=normalize_fields(extracted["fields"]))

        stored = store.add_document(user["id"], **document)
        if stored is None:
            # The same file arrived twice at once. Show the copy that was saved first.
            response.status_code = 200
            return {**full(store.find_document_by_hash(user["id"], sha256)), "duplicate": True}
        return {**full(stored), "duplicate": False}

    @app.get("/api/documents")
    def list_documents(user=Depends(current_user)):
        return {"documents": [public_document(d) for d in store.list_documents(user["id"])]}

    @app.get("/api/documents/{document_id}")
    def get_document(document_id: str, user=Depends(current_user)):
        return full(own_document(document_id, user))

    @app.patch("/api/documents/{document_id}/fields")
    def edit_fields(document_id: str, body: FieldEdits, user=Depends(current_user)):
        document = own_document(document_id, user)
        if document["status"] == "syncing" and not is_stale_sync(document):
            raise HTTPException(status_code=409, detail="Wait for the send to HubSpot to finish before editing.")
        for key, value in body.fields.items():
            check_edit(key, value)
        fields = apply_edits(document["fields"], body.fields)
        # A changed document can be sent again, which updates the same HubSpot records.
        allowed = ("needs_review", "failed", "synced", "sync_failed") + (("syncing",) if is_stale_sync(document) else ())
        updated = store.update_document(document["id"], only_if_status=allowed, fields=fields, status="needs_review", error=None)
        if updated is None:
            raise HTTPException(status_code=409, detail="Wait for the send to HubSpot to finish before editing.")
        return full(updated)

    @app.post("/api/documents/{document_id}/sync")
    def sync(document_id: str, user=Depends(current_user)):
        document = own_document(document_id, user)
        missing = missing_for_sync(document["fields"])
        if missing:
            return JSONResponse(status_code=422, content={"detail": NEEDS_EMAIL, "missing": missing})
        use_n8n = bool(settings["n8n_webhook_url"])
        if not use_n8n and not settings["hubspot_token"]:
            raise HTTPException(status_code=503, detail="HubSpot isn't connected yet.")
        if document["status"] == "failed":
            raise HTTPException(status_code=409, detail="Check the fields and save them before sending.")
        if document["status"] == "synced":
            raise HTTPException(status_code=409, detail="This is already in HubSpot. Change a field to send it again.")
        enforce_limit("sync_user", user["id"])
        if not store.claim_sync(document["id"]):
            raise HTTPException(status_code=409, detail="This document is already being sent to HubSpot.")

        record = to_crm_record(document["fields"], document["doc_type"])
        source_note = f"Imported by Inkfold from {document['filename']}."
        record["note"] = f"{record['note']}\n\n{source_note}" if record["note"] else source_note

        if use_n8n:
            payload = build_n8n_payload(
                str(document["id"]), record, settings["public_api_url"] + "/api/sync/callback", document["crm"]
            )
            store.add_sync_event(document["id"], "n8n", "sent", "Sent to the n8n workflow.")
            try:
                send_to_n8n(settings["n8n_webhook_url"], settings["n8n_shared_secret"], payload, client=http)
            except SyncError as error:
                store.add_sync_event(document["id"], "n8n", "failed", str(error))
                store.update_document(document["id"], status="sync_failed", error=str(error))
            return full(store.get_document(document["id"]))

        hubspot = HubSpot(
            settings["hubspot_token"],
            portal_id=settings["hubspot_portal_id"] or None,
            deal_stage=settings["hubspot_deal_stage"],
            client=http,
            base_url=settings.get("hubspot_base_url") or None,
        )
        try:
            crm = hubspot.push(record, existing=document["crm"])
        except CrmError as error:
            store.add_sync_event(document["id"], "direct", "failed", error.message)
            store.update_document(document["id"], status="sync_failed", error=error.message)
        else:
            store.add_sync_event(document["id"], "direct", "synced", crm_summary(crm))
            store.update_document(document["id"], status="synced", crm=crm, error=None)
        return full(store.get_document(document["id"]))

    @app.post("/api/sync/callback")
    async def sync_callback(request: Request):
        body = await request.body()
        if not verify_signature(
            settings["n8n_shared_secret"],
            request.headers.get(TIMESTAMP_HEADER),
            request.headers.get(SIGNATURE_HEADER),
            body,
        ):
            raise HTTPException(status_code=401, detail="Invalid signature.")
        try:
            result = parse_callback(body)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid callback body.")
        document_id = parse_id(result.document_id)
        if store.get_document(document_id) is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        # Only a document still waiting is changed, so a repeated callback does nothing.
        if result.status == "synced":
            crm = {key: result.crm.get(key) for key in ("contact_id", "company_id", "deal_id", "note_id", "contact_url", "deal_url")}
            applied = store.update_document(document_id, only_if_status=("syncing",), status="synced", crm=crm, error=None)
            detail = crm_summary(crm)
        else:
            detail = result.detail or "The n8n workflow couldn't save the record."
            applied = store.update_document(document_id, only_if_status=("syncing",), status="sync_failed", error=detail)
        if applied:
            store.add_sync_event(document_id, "n8n", result.status, detail)
        return {"ok": True}

    @app.delete("/api/documents/{document_id}", status_code=204)
    def delete_document(document_id: str, user=Depends(current_user)):
        if not store.delete_document(parse_id(document_id), user["id"]):
            raise HTTPException(status_code=404, detail="Document not found.")
        return Response(status_code=204)

    return app
