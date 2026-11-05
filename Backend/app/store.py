import hashlib
import hmac
import os
import secrets
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

# Longer than a session token lasts, so nobody loses a demo they are still using.
DEMO_LIFETIME = timedelta(days=2)
# A send that never heard back from n8n can be tried again after this long.
SYNC_STALE_AFTER = timedelta(minutes=10)
# Short, because anyone who sees the link in the inbox can use it.
RESET_TOKEN_LIFETIME = timedelta(minutes=15)
# How long a request waits for the first database connection before giving up.
POOL_OPEN_TIMEOUT = 10
USER_COLUMNS = "id, email, is_demo, password_changed_at, password_hash IS NOT NULL AS has_password"
DOCUMENT_COLUMNS = "id, user_id, filename, mime, size, sha256, doc_type, status, error, fields, crm, source, created_at, updated_at"
# An email whose check never finished, say because the function timed out, is tried again after this long.
INTAKE_STALE_AFTER = timedelta(minutes=10)
INTAKE_HISTORY = timedelta(days=30)
# Originals are kept for checking fields against, not as an archive, so the database stays small.
FILE_LIFETIME = timedelta(days=30)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT,
    is_demo BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    mime TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    fields JSONB NOT NULL,
    crm JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, sha256)
);
CREATE INDEX IF NOT EXISTS documents_user_idx ON documents (user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS sync_events (
    id BIGSERIAL PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sync_events_document_idx ON sync_events (document_id, id);
CREATE TABLE IF NOT EXISTS rate_limits (
    key TEXT PRIMARY KEY,
    hits INTEGER NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS intake_token TEXT UNIQUE;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source JSONB;
CREATE TABLE IF NOT EXISTS intake_messages (
    key TEXT PRIMARY KEY,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    sender TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    document_ids JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS intake_messages_user_idx ON intake_messages (user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS document_files (
    document_id UUID PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    data BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL
);
"""


def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"{salt.hex()}:{digest.hex()}"


def verify_password(password, stored):
    salt, digest = stored.split(":")
    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
    return hmac.compare_digest(candidate.hex(), digest)


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


# Compared against when the email is unknown, so response time doesn't reveal which accounts exist.
_DUMMY_HASH = hash_password(uuid.uuid4().hex)


class Store:
    def __init__(self, database_url):
        self.database_url = database_url
        self._pool = None

    def _get_pool(self):
        if self._pool is None:
            pool = ConnectionPool(
                self.database_url,
                open=False,
                min_size=1,
                max_size=5,
                # Neon closes idle connections when it scales to zero.
                check=ConnectionPool.check_connection,
                # PgBouncer-style poolers share server connections across clients.
                kwargs={"row_factory": dict_row, "prepare_threshold": None},
            )
            try:
                pool.open(wait=True, timeout=POOL_OPEN_TIMEOUT)
            except Exception:
                # A pool that failed to open is closed for good, so drop it and let the next request try again.
                pool.close()
                raise
            self._pool = pool
        return self._pool

    def close(self):
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    def init_schema(self):
        with psycopg.connect(self.database_url, autocommit=True) as db:
            db.execute(SCHEMA)

    @contextmanager
    def _connect(self):
        with self._get_pool().connection() as db:
            yield db

    # Users

    def create_user(self, email, password):
        """Returns the new user, or None if the email is taken."""
        with self._connect() as db:
            return db.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s) "
                f"ON CONFLICT (email) DO NOTHING RETURNING {USER_COLUMNS}",
                (uuid.uuid4(), email.lower(), hash_password(password)),
            ).fetchone()

    def authenticate(self, email, password):
        with self._connect() as db:
            user = db.execute(
                f"SELECT {USER_COLUMNS}, password_hash FROM users WHERE email = %s", (email.lower(),)
            ).fetchone()
        stored = user["password_hash"] if user and user["password_hash"] else _DUMMY_HASH
        if not verify_password(password, stored) or not user or not user["password_hash"]:
            return None
        return {key: value for key, value in user.items() if key != "password_hash"}

    def get_user(self, user_id):
        with self._connect() as db:
            return db.execute(f"SELECT {USER_COLUMNS} FROM users WHERE id = %s", (user_id,)).fetchone()

    @staticmethod
    def _end_sessions(db, user_id):
        """Tokens issued before now stop working."""
        db.execute("UPDATE users SET password_changed_at = %s WHERE id = %s", (datetime.now(timezone.utc), user_id))

    def set_password(self, user_id, password):
        with self._connect() as db:
            db.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s AND NOT is_demo", (hash_password(password), user_id)
            )
            self._end_sessions(db, user_id)

    def find_or_create_verified_user(self, email):
        """The account for an email that Google has confirmed, created without a password if it's new."""
        email = email.lower()
        with self._connect() as db:
            created = db.execute(
                "INSERT INTO users (id, email, email_verified) VALUES (%s, %s, true) "
                f"ON CONFLICT (email) DO NOTHING RETURNING {USER_COLUMNS}",
                (uuid.uuid4(), email),
            ).fetchone()
            if created:
                return created
            existing = db.execute(
                "SELECT id, is_demo, email_verified, password_hash FROM users WHERE email = %s FOR UPDATE", (email,)
            ).fetchone()
            if existing["is_demo"]:
                return None
            if not existing["email_verified"]:
                # Sign-up doesn't prove who owns an email, so a password set before now may be a stranger's.
                db.execute("UPDATE users SET email_verified = true, password_hash = NULL WHERE id = %s", (existing["id"],))
                if existing["password_hash"]:
                    self._end_sessions(db, existing["id"])
            return db.execute(f"SELECT {USER_COLUMNS} FROM users WHERE id = %s", (existing["id"],)).fetchone()

    def create_reset_token(self, email):
        """Return a new single-use reset token, or None if there's no such account or it's a demo."""
        token = secrets.token_urlsafe(32)
        with self._connect() as db:
            user = db.execute("SELECT id FROM users WHERE email = %s AND NOT is_demo", (email.lower(),)).fetchone()
            if user is None:
                return None
            db.execute("DELETE FROM password_reset_tokens WHERE user_id = %s OR expires_at < now()", (user["id"],))
            # Only a hash is stored, so reading the database doesn't give anyone a working link.
            db.execute(
                "INSERT INTO password_reset_tokens (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
                (_token_hash(token), user["id"], datetime.now(timezone.utc) + RESET_TOKEN_LIFETIME),
            )
        return token

    def reset_password(self, token, password):
        """Use up a reset token to set a new password. False if the token is invalid or expired."""
        password_hash = hash_password(password)
        with self._connect() as db:
            row = db.execute(
                "DELETE FROM password_reset_tokens WHERE token_hash = %s RETURNING user_id, expires_at > now() AS valid",
                (_token_hash(token),),
            ).fetchone()
            if row is None or not row["valid"]:
                return False
            # The link reached this inbox, which proves who owns the email.
            db.execute(
                "UPDATE users SET password_hash = %s, email_verified = true WHERE id = %s",
                (password_hash, row["user_id"]),
            )
            self._end_sessions(db, row["user_id"])
        return True

    def create_demo_user(self, documents):
        """A private demo account with the sample documents already read. It can't be signed into with a password."""
        user_id = uuid.uuid4()
        with self._connect() as db:
            db.execute(
                "DELETE FROM users WHERE is_demo AND created_at < %s",
                (datetime.now(timezone.utc) - DEMO_LIFETIME,),
            )
            db.execute("DELETE FROM rate_limits WHERE expires_at < now()")
            user = db.execute(
                f"INSERT INTO users (id, email, is_demo) VALUES (%s, %s, true) RETURNING {USER_COLUMNS}",
                (user_id, f"demo-{user_id.hex[:12]}@inkfold.demo"),
            ).fetchone()
            for document in documents:
                self._insert_document(db, user_id, **document)
        return user

    # Email intake

    def intake_token(self, user_id, new_token, replace=False):
        """The account's intake token. `new_token` is used if it has none yet, or always with `replace`."""
        with self._connect() as db:
            row = db.execute(
                "UPDATE users SET intake_token = CASE WHEN %(replace)s OR intake_token IS NULL THEN %(token)s "
                "ELSE intake_token END WHERE id = %(id)s RETURNING intake_token",
                {"replace": replace, "token": new_token, "id": user_id},
            ).fetchone()
        return row["intake_token"] if row else None

    def user_by_intake_token(self, token):
        with self._connect() as db:
            return db.execute(f"SELECT {USER_COLUMNS} FROM users WHERE intake_token = %s", (token,)).fetchone()

    def claim_intake_message(self, key):
        """'claimed' to go ahead, 'done' if it was already handled, or 'busy' if another check has it."""
        with self._connect() as db:
            db.execute("DELETE FROM intake_messages WHERE created_at < %s", (datetime.now(timezone.utc) - INTAKE_HISTORY,))
            claimed = db.execute(
                "INSERT INTO intake_messages AS m (key, status) VALUES (%s, 'processing') "
                "ON CONFLICT (key) DO UPDATE SET created_at = now() "
                "WHERE m.status = 'processing' AND m.created_at < %s RETURNING key",
                (key, datetime.now(timezone.utc) - INTAKE_STALE_AFTER),
            ).fetchone()
            if claimed:
                return "claimed"
            row = db.execute("SELECT status FROM intake_messages WHERE key = %s", (key,)).fetchone()
        return "busy" if row and row["status"] == "processing" else "done"

    def finish_intake_message(self, key, *, user_id, sender, subject, status, detail, document_ids):
        with self._connect() as db:
            db.execute(
                "UPDATE intake_messages SET user_id = %s, sender = %s, subject = %s, status = %s, detail = %s, "
                "document_ids = %s WHERE key = %s",
                (user_id, sender, subject, status, detail, Jsonb([str(i) for i in document_ids]), key),
            )

    def release_intake_message(self, key):
        """Let a later check take this email, because this one stopped before reaching it."""
        with self._connect() as db:
            db.execute("DELETE FROM intake_messages WHERE key = %s AND status = 'processing'", (key,))

    def recent_intake(self, user_id, limit=5):
        with self._connect() as db:
            return db.execute(
                "SELECT sender, subject, status, detail, document_ids, created_at FROM intake_messages "
                "WHERE user_id = %s AND status <> 'processing' ORDER BY created_at DESC LIMIT %s",
                (user_id, limit),
            ).fetchall()

    # Rate limits

    def hit_rate_limit(self, key, limit, window):
        """Count one hit. Returns seconds to wait if the limit is exceeded, otherwise None."""
        with self._connect() as db:
            row = db.execute(
                "INSERT INTO rate_limits AS r (key, hits, expires_at) VALUES (%(key)s, 1, now() + %(window)s) "
                "ON CONFLICT (key) DO UPDATE SET "
                "hits = CASE WHEN r.expires_at <= now() THEN 1 ELSE r.hits + 1 END, "
                "expires_at = CASE WHEN r.expires_at <= now() THEN EXCLUDED.expires_at ELSE r.expires_at END "
                "RETURNING hits, EXTRACT(EPOCH FROM expires_at - now()) AS wait",
                {"key": key, "window": window},
            ).fetchone()
        return max(1, int(row["wait"])) if row["hits"] > limit else None

    # Documents

    @staticmethod
    def _insert_document(db, user_id, filename, mime, size, sha256, doc_type, status, fields, error=None, source=None):
        return db.execute(
            f"INSERT INTO documents (id, user_id, filename, mime, size, sha256, doc_type, status, error, fields, source) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            f"ON CONFLICT (user_id, sha256) DO NOTHING RETURNING {DOCUMENT_COLUMNS}",
            (uuid.uuid4(), user_id, filename, mime, size, sha256, doc_type, status, error, Jsonb(fields),
             Jsonb(source) if source else None),
        ).fetchone()

    def add_document(self, user_id, data=None, **document):
        """Returns the new document, or None if this user already uploaded the same file. `data` keeps the original."""
        with self._connect() as db:
            stored = self._insert_document(db, user_id, **document)
            if stored and data:
                db.execute("DELETE FROM document_files WHERE created_at < %s", (datetime.now(timezone.utc) - FILE_LIFETIME,))
                db.execute("INSERT INTO document_files (document_id, data) VALUES (%s, %s)", (stored["id"], data))
            return stored

    def document_file(self, document_id):
        """The original file's bytes, or None if it isn't kept."""
        with self._connect() as db:
            row = db.execute("SELECT data FROM document_files WHERE document_id = %s", (document_id,)).fetchone()
        return bytes(row["data"]) if row else None

    def find_document_by_hash(self, user_id, sha256):
        with self._connect() as db:
            return db.execute(
                f"SELECT {DOCUMENT_COLUMNS} FROM documents WHERE user_id = %s AND sha256 = %s", (user_id, sha256)
            ).fetchone()

    def list_documents(self, user_id):
        with self._connect() as db:
            return db.execute(
                f"SELECT {DOCUMENT_COLUMNS} FROM documents WHERE user_id = %s ORDER BY created_at DESC, id", (user_id,)
            ).fetchall()

    def get_document(self, document_id, user_id=None):
        """Pass user_id to only find that user's document."""
        query = f"SELECT {DOCUMENT_COLUMNS} FROM documents WHERE id = %s"
        params = [document_id]
        if user_id is not None:
            query += " AND user_id = %s"
            params.append(user_id)
        with self._connect() as db:
            return db.execute(query, params).fetchone()

    def update_document(self, document_id, *, only_if_status=None, **changes):
        """Returns the updated document, or None if it wasn't in one of `only_if_status`."""
        values = {key: Jsonb(value) if key in ("fields", "crm") and value is not None else value for key, value in changes.items()}
        assignments = ", ".join(f"{key} = %({key})s" for key in values)
        query = f"UPDATE documents SET {assignments}, updated_at = now() WHERE id = %(id)s"
        if only_if_status:
            query += " AND status = ANY(%(only_if_status)s)"
        with self._connect() as db:
            return db.execute(
                query + f" RETURNING {DOCUMENT_COLUMNS}",
                {**values, "id": document_id, "only_if_status": list(only_if_status or ())},
            ).fetchone()

    def claim_sync(self, document_id):
        """Marks the document as being sent. False if another send is already under way.

        Done in one statement so two clicks at once can't both reach HubSpot.
        """
        with self._connect() as db:
            row = db.execute(
                "UPDATE documents SET status = 'syncing', error = NULL, updated_at = now() WHERE id = %s "
                "AND (status IN ('needs_review', 'sync_failed') OR (status = 'syncing' AND updated_at < %s)) RETURNING id",
                (document_id, datetime.now(timezone.utc) - SYNC_STALE_AFTER),
            ).fetchone()
        return row is not None

    def delete_document(self, document_id, user_id):
        with self._connect() as db:
            return db.execute("DELETE FROM documents WHERE id = %s AND user_id = %s", (document_id, user_id)).rowcount > 0

    def add_sync_event(self, document_id, channel, status, detail=""):
        with self._connect() as db:
            db.execute(
                "INSERT INTO sync_events (document_id, channel, status, detail) VALUES (%s, %s, %s, %s)",
                (document_id, channel, status, detail),
            )

    def sync_events(self, document_id):
        with self._connect() as db:
            return db.execute(
                "SELECT id, channel, status, detail, created_at FROM sync_events WHERE document_id = %s ORDER BY id",
                (document_id,),
            ).fetchall()
