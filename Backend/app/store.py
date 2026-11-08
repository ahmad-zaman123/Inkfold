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
# Long enough for a teammate to get round to it, short enough that a forgotten link stops working.
INVITE_LIFETIME = timedelta(days=7)
# How long a request waits for the first database connection before giving up.
POOL_OPEN_TIMEOUT = 10
USER_COLUMNS = (
    "id, email, is_demo, password_changed_at, password_hash IS NOT NULL AS has_password, team_id, team_role, "
    "(SELECT count(*) FROM users m WHERE m.team_id = users.team_id) AS team_size"
)
DOCUMENT_COLUMNS = (
    "id, user_id, team_id, filename, mime, size, sha256, doc_type, status, error, fields, crm, source, created_at, updated_at, "
    "(SELECT email FROM users WHERE users.id = documents.user_id) AS added_by"
)
MEMBER_COLUMNS = "id, email, team_role, is_demo"
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
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
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
-- Teams. Documents belong to a team, and everyone in it reviews them. Each account starts in a team of its own,
-- which keeps the account's id, so accounts from before teams existed keep their documents.
CREATE TABLE IF NOT EXISTS teams (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE users ADD COLUMN IF NOT EXISTS team_id UUID REFERENCES teams(id);
ALTER TABLE users ADD COLUMN IF NOT EXISTS team_role TEXT NOT NULL DEFAULT 'owner';
INSERT INTO teams (id) SELECT id FROM users WHERE team_id IS NULL ON CONFLICT (id) DO NOTHING;
UPDATE users SET team_id = id WHERE team_id IS NULL;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS team_id UUID REFERENCES teams(id) ON DELETE CASCADE;
UPDATE documents d SET team_id = u.team_id FROM users u WHERE d.user_id = u.id AND d.team_id IS NULL;
-- A file is a duplicate when anyone in the team has uploaded it.
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_user_id_sha256_key;
CREATE UNIQUE INDEX IF NOT EXISTS documents_team_file_idx ON documents (team_id, sha256);
CREATE INDEX IF NOT EXISTS documents_team_idx ON documents (team_id, created_at DESC);
ALTER TABLE sync_events ADD COLUMN IF NOT EXISTS actor TEXT NOT NULL DEFAULT '';
-- Where the team sends documents: hubspot, pipedrive or sheets. Empty means the first one the server has.
ALTER TABLE teams ADD COLUMN IF NOT EXISTS destination TEXT NOT NULL DEFAULT '';
CREATE TABLE IF NOT EXISTS team_invites (
    id UUID PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    invited_by UUID REFERENCES users(id) ON DELETE SET NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (team_id, email)
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

    @staticmethod
    def _insert_user(db, email, password_hash=None, is_demo=False, email_verified=False, user_id=None):
        """A new account in a team of its own, or None if the email is taken."""
        user_id = user_id or uuid.uuid4()
        db.execute("INSERT INTO teams (id) VALUES (%s)", (user_id,))
        user = db.execute(
            "INSERT INTO users (id, email, password_hash, is_demo, email_verified, team_id) VALUES (%s, %s, %s, %s, %s, %s) "
            f"ON CONFLICT (email) DO NOTHING RETURNING {USER_COLUMNS}",
            (user_id, email, password_hash, is_demo, email_verified, user_id),
        ).fetchone()
        if user is None:
            db.execute("DELETE FROM teams WHERE id = %s", (user_id,))
        return user

    def create_user(self, email, password):
        """Returns the new user, or None if the email is taken."""
        password_hash = hash_password(password)
        with self._connect() as db:
            return self._insert_user(db, email.lower(), password_hash)

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
            created = self._insert_user(db, email, email_verified=True)
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
            # Demo accounts can't share, so their teams go with them.
            db.execute("DELETE FROM teams t WHERE NOT EXISTS (SELECT 1 FROM users WHERE users.team_id = t.id)")
            db.execute("DELETE FROM rate_limits WHERE expires_at < now()")
            user = self._insert_user(db, f"demo-{user_id.hex[:12]}@inkfold.demo", is_demo=True, user_id=user_id)
            for document in documents:
                self._insert_document(db, user_id, user_id, **document)
        return user

    # Teams

    def team(self, team_id):
        with self._connect() as db:
            return db.execute("SELECT id, name, destination FROM teams WHERE id = %s", (team_id,)).fetchone()

    def team_members(self, team_id):
        with self._connect() as db:
            return db.execute(
                f"SELECT {MEMBER_COLUMNS} FROM users WHERE team_id = %s ORDER BY team_role DESC, created_at, email", (team_id,)
            ).fetchall()

    def set_destination(self, team_id, destination):
        with self._connect() as db:
            db.execute("UPDATE teams SET destination = %s WHERE id = %s", (destination, team_id))

    def rename_team(self, team_id, name):
        with self._connect() as db:
            db.execute("UPDATE teams SET name = %s WHERE id = %s", (name, team_id))

    def create_invite(self, team_id, email, invited_by):
        """A new invite link for `email`. Inviting the same address again replaces the old link."""
        token = secrets.token_urlsafe(32)
        with self._connect() as db:
            db.execute("DELETE FROM team_invites WHERE expires_at < now()")
            invite = db.execute(
                "INSERT INTO team_invites (id, token_hash, team_id, email, invited_by, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (team_id, email) DO UPDATE SET "
                "token_hash = EXCLUDED.token_hash, invited_by = EXCLUDED.invited_by, "
                "expires_at = EXCLUDED.expires_at, created_at = now() RETURNING id, email, expires_at",
                (uuid.uuid4(), _token_hash(token), team_id, email.lower(), invited_by,
                 datetime.now(timezone.utc) + INVITE_LIFETIME),
            ).fetchone()
        return token, invite

    def team_invites(self, team_id):
        with self._connect() as db:
            return db.execute(
                "SELECT id, email, expires_at FROM team_invites WHERE team_id = %s AND expires_at > now() ORDER BY created_at",
                (team_id,),
            ).fetchall()

    def delete_invite(self, team_id, invite_id):
        with self._connect() as db:
            return db.execute("DELETE FROM team_invites WHERE id = %s AND team_id = %s", (invite_id, team_id)).rowcount > 0

    def find_invite(self, token):
        """The invite behind a link, with its team's name and who sent it, or None if it's unknown or expired."""
        with self._connect() as db:
            return db.execute(
                "SELECT i.id, i.team_id, i.email, t.name AS team_name, u.email AS invited_by, "
                "(SELECT count(*) FROM users m WHERE m.team_id = i.team_id) AS team_size "
                "FROM team_invites i JOIN teams t ON t.id = i.team_id LEFT JOIN users u ON u.id = i.invited_by "
                "WHERE i.token_hash = %s AND i.expires_at > now()",
                (_token_hash(token),),
            ).fetchone()

    def join_team(self, user_id, token):
        """Use an invite. Returns ("joined", documents moved), or a reason it can't be used:
        "invalid", "wrong_email" or "owner_with_members"."""
        with self._connect() as db:
            invite = db.execute(
                "SELECT id, team_id, email FROM team_invites WHERE token_hash = %s AND expires_at > now() FOR UPDATE",
                (_token_hash(token),),
            ).fetchone()
            if invite is None:
                return "invalid", 0
            user = db.execute("SELECT id, email, team_id, team_role FROM users WHERE id = %s FOR UPDATE", (user_id,)).fetchone()
            if user["email"] != invite["email"]:
                return "wrong_email", 0
            old, new, moved = user["team_id"], invite["team_id"], 0
            if old != new:
                others = db.execute(
                    "SELECT count(*) AS n FROM users WHERE team_id = %s AND id <> %s", (old, user_id)
                ).fetchone()["n"]
                if others and user["team_role"] == "owner":
                    return "owner_with_members", 0
                db.execute("UPDATE users SET team_id = %s, team_role = 'member' WHERE id = %s", (new, user_id))
                if not others:
                    # Someone working alone brings their documents. Files the team already has stay as the team's copy.
                    db.execute(
                        "DELETE FROM documents d WHERE d.team_id = %s AND EXISTS "
                        "(SELECT 1 FROM documents t WHERE t.team_id = %s AND t.sha256 = d.sha256)",
                        (old, new),
                    )
                    moved = db.execute("UPDATE documents SET team_id = %s WHERE team_id = %s", (new, old)).rowcount
                    db.execute("DELETE FROM teams WHERE id = %s", (old,))
            db.execute("DELETE FROM team_invites WHERE id = %s", (invite["id"],))
        return "joined", moved

    def leave_team(self, user_id, team_id):
        """Move a member out of `team_id` into a team of their own. The documents stay with the team.

        False if they aren't a member of that team, or are its owner.
        """
        new = uuid.uuid4()
        with self._connect() as db:
            db.execute("INSERT INTO teams (id) VALUES (%s)", (new,))
            moved = db.execute(
                "UPDATE users SET team_id = %s, team_role = 'owner' WHERE id = %s AND team_id = %s AND team_role = 'member'",
                (new, user_id, team_id),
            ).rowcount
            if not moved:
                db.execute("DELETE FROM teams WHERE id = %s", (new,))
        return moved > 0

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
    def _insert_document(db, user_id, team_id, filename, mime, size, sha256, doc_type, status, fields, error=None, source=None):
        return db.execute(
            f"INSERT INTO documents (id, user_id, team_id, filename, mime, size, sha256, doc_type, status, error, fields, source) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            f"ON CONFLICT (team_id, sha256) DO NOTHING RETURNING {DOCUMENT_COLUMNS}",
            (uuid.uuid4(), user_id, team_id, filename, mime, size, sha256, doc_type, status, error, Jsonb(fields),
             Jsonb(source) if source else None),
        ).fetchone()

    def add_document(self, user, data=None, **document):
        """Returns the new document, or None if the team already has the same file. `data` keeps the original."""
        with self._connect() as db:
            stored = self._insert_document(db, user["id"], user["team_id"], **document)
            if stored and data:
                db.execute("DELETE FROM document_files WHERE created_at < %s", (datetime.now(timezone.utc) - FILE_LIFETIME,))
                db.execute("INSERT INTO document_files (document_id, data) VALUES (%s, %s)", (stored["id"], data))
            return stored

    def document_file(self, document_id):
        """The original file's bytes, or None if it isn't kept."""
        with self._connect() as db:
            row = db.execute("SELECT data FROM document_files WHERE document_id = %s", (document_id,)).fetchone()
        return bytes(row["data"]) if row else None

    def find_document_by_hash(self, team_id, sha256):
        with self._connect() as db:
            return db.execute(
                f"SELECT {DOCUMENT_COLUMNS} FROM documents WHERE team_id = %s AND sha256 = %s", (team_id, sha256)
            ).fetchone()

    def list_documents(self, team_id):
        with self._connect() as db:
            return db.execute(
                f"SELECT {DOCUMENT_COLUMNS} FROM documents WHERE team_id = %s ORDER BY created_at DESC, id", (team_id,)
            ).fetchall()

    def get_document(self, document_id, team_id=None):
        """Pass team_id to only find that team's document."""
        query = f"SELECT {DOCUMENT_COLUMNS} FROM documents WHERE id = %s"
        params = [document_id]
        if team_id is not None:
            query += " AND team_id = %s"
            params.append(team_id)
        with self._connect() as db:
            return db.execute(query, params).fetchone()

    def update_document(self, document_id, *, only_if_status=None, only_if_updated_at=None, **changes):
        """Returns the updated document, or None if it wasn't in one of `only_if_status`.

        With `only_if_updated_at`, also None if someone changed it since then.
        """
        values = {key: Jsonb(value) if key in ("fields", "crm") and value is not None else value for key, value in changes.items()}
        assignments = ", ".join(f"{key} = %({key})s" for key in values)
        query = f"UPDATE documents SET {assignments}, updated_at = now() WHERE id = %(id)s"
        if only_if_status:
            query += " AND status = ANY(%(only_if_status)s)"
        if only_if_updated_at:
            query += " AND updated_at = %(only_if_updated_at)s"
        with self._connect() as db:
            return db.execute(
                query + f" RETURNING {DOCUMENT_COLUMNS}",
                {**values, "id": document_id, "only_if_status": list(only_if_status or ()), "only_if_updated_at": only_if_updated_at},
            ).fetchone()

    def claim_sync(self, document_id, allow_synced=False):
        """Marks the document as being sent. False if another send is already under way.

        Done in one statement so two clicks at once can't both reach HubSpot. `allow_synced` lets a document
        already sent somewhere else go to a new destination.
        """
        ready = ["needs_review", "sync_failed"] + (["synced"] if allow_synced else [])
        with self._connect() as db:
            row = db.execute(
                "UPDATE documents SET status = 'syncing', error = NULL, updated_at = now() WHERE id = %s "
                "AND (status = ANY(%s) OR (status = 'syncing' AND updated_at < %s)) RETURNING id",
                (document_id, ready, datetime.now(timezone.utc) - SYNC_STALE_AFTER),
            ).fetchone()
        return row is not None

    def delete_document(self, document_id, team_id):
        with self._connect() as db:
            return db.execute("DELETE FROM documents WHERE id = %s AND team_id = %s", (document_id, team_id)).rowcount > 0

    def add_sync_event(self, document_id, channel, status, detail="", actor=""):
        with self._connect() as db:
            db.execute(
                "INSERT INTO sync_events (document_id, channel, status, detail, actor) VALUES (%s, %s, %s, %s, %s)",
                (document_id, channel, status, detail, actor),
            )

    def sync_events(self, document_id):
        with self._connect() as db:
            return db.execute(
                "SELECT id, channel, status, detail, actor, created_at FROM sync_events WHERE document_id = %s ORDER BY id",
                (document_id,),
            ).fetchall()
