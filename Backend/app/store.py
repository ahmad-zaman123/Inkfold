import hashlib
import hmac
import os
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
# How long a request waits for the first database connection before giving up.
POOL_OPEN_TIMEOUT = 10
DOCUMENT_COLUMNS = "id, user_id, filename, mime, size, sha256, doc_type, status, error, fields, crm, created_at, updated_at"

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
"""


def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"{salt.hex()}:{digest.hex()}"


def verify_password(password, stored):
    salt, digest = stored.split(":")
    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
    return hmac.compare_digest(candidate.hex(), digest)


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
                "ON CONFLICT (email) DO NOTHING RETURNING id, email, is_demo",
                (uuid.uuid4(), email.lower(), hash_password(password)),
            ).fetchone()

    def authenticate(self, email, password):
        with self._connect() as db:
            user = db.execute(
                "SELECT id, email, is_demo, password_hash FROM users WHERE email = %s", (email.lower(),)
            ).fetchone()
        stored = user["password_hash"] if user and user["password_hash"] else _DUMMY_HASH
        if not verify_password(password, stored) or not user or not user["password_hash"]:
            return None
        return {key: user[key] for key in ("id", "email", "is_demo")}

    def get_user(self, user_id):
        with self._connect() as db:
            return db.execute("SELECT id, email, is_demo FROM users WHERE id = %s", (user_id,)).fetchone()

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
                "INSERT INTO users (id, email, is_demo) VALUES (%s, %s, true) RETURNING id, email, is_demo",
                (user_id, f"demo-{user_id.hex[:12]}@inkfold.demo"),
            ).fetchone()
            for document in documents:
                self._insert_document(db, user_id, **document)
        return user

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
    def _insert_document(db, user_id, filename, mime, size, sha256, doc_type, status, fields, error=None):
        return db.execute(
            f"INSERT INTO documents (id, user_id, filename, mime, size, sha256, doc_type, status, error, fields) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            f"ON CONFLICT (user_id, sha256) DO NOTHING RETURNING {DOCUMENT_COLUMNS}",
            (uuid.uuid4(), user_id, filename, mime, size, sha256, doc_type, status, error, Jsonb(fields)),
        ).fetchone()

    def add_document(self, user_id, **document):
        """Returns the new document, or None if this user already uploaded the same file."""
        with self._connect() as db:
            return self._insert_document(db, user_id, **document)

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
