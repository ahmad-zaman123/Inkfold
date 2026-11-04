import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
# Each model has its own daily limit, so later ones keep uploads working after the first runs out.
DEFAULT_EXTRACT_MODELS = (
    "models/gemini-3.5-flash-lite",
    "models/gemini-3.1-flash-lite",
    "models/gemini-2.5-flash-lite",
    "models/gemini-2.5-flash",
)


def _list(name, default=()):
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()] or list(default)


def load_settings():
    load_dotenv(BASE_DIR / ".env")
    cors_origins = _list("CORS_ALLOWED_ORIGINS")
    # Links in emails point at the frontend, the first allowed origin.
    app_url = (cors_origins[0] if cors_origins else "http://localhost:5180").rstrip("/")
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    intake_inbox = os.environ.get("INTAKE_EMAIL", "")
    intake_password = os.environ.get("INTAKE_PASSWORD", "")
    missing = [n for n in ("DATABASE_URL", "JWT_SECRET", "GEMINI_API_KEY") if not os.environ.get(n)]
    n8n_url = os.environ.get("N8N_WEBHOOK_URL", "")
    if n8n_url and not os.environ.get("N8N_SHARED_SECRET"):
        missing.append("N8N_SHARED_SECRET")
    if missing:
        raise RuntimeError(f"Missing required settings: {', '.join(missing)}")
    return {
        "database_url": os.environ["DATABASE_URL"],
        "jwt_secret": os.environ["JWT_SECRET"],
        "gemini_api_key": os.environ["GEMINI_API_KEY"],
        "extract_models": _list("GEMINI_EXTRACT_MODELS", DEFAULT_EXTRACT_MODELS),
        "hubspot_token": os.environ.get("HUBSPOT_TOKEN", ""),
        "hubspot_portal_id": os.environ.get("HUBSPOT_PORTAL_ID", ""),
        "hubspot_deal_stage": os.environ.get("HUBSPOT_DEAL_STAGE", "closedwon"),
        # Only changed to point at a local stand-in for HubSpot when testing.
        "hubspot_base_url": os.environ.get("HUBSPOT_BASE_URL", ""),
        "n8n_webhook_url": n8n_url,
        "n8n_shared_secret": os.environ.get("N8N_SHARED_SECRET", ""),
        # n8n posts its result here, so it must be an address n8n can reach.
        "public_api_url": os.environ.get("PUBLIC_API_URL", "http://localhost:8020").rstrip("/"),
        "cors_origins": cors_origins,
        "app_url": app_url,
        # The inbox that forwarded documents arrive in. Without it, email intake is hidden.
        "intake": (intake_inbox and intake_password and {
            "inbox": intake_inbox,
            "host": os.environ.get("INTAKE_IMAP_HOST", "imap.gmail.com"),
            "user": intake_inbox,
            "password": intake_password,
        }) or None,
        # Lets a scheduled job check the inbox. Without it, only the "Check inbox" button does.
        "intake_poll_secret": os.environ.get("INTAKE_POLL_SECRET", ""),
        # Without it, Continue with Google is turned off.
        "google_client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
        # Without these, password reset is hidden.
        "smtp": (smtp_user and smtp_password and {
            "host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
            "port": int(os.environ.get("SMTP_PORT", "587")),
            "user": smtp_user,
            "password": smtp_password,
            "sender": os.environ.get("EMAIL_FROM") or smtp_user,
            "logo_url": f"{app_url}/email-logo.png",
        }) or None,
        "uploads_per_user_day": int(os.environ.get("UPLOADS_PER_USER_DAY", "10")),
        "uploads_per_day": int(os.environ.get("UPLOADS_PER_DAY", "100")),
        # Vercel sets the client address itself. Elsewhere the header could be forged.
        "behind_proxy": bool(os.environ.get("VERCEL")),
    }
