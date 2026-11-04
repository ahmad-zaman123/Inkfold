"""Email intake: documents forwarded to a per-account address land in Inkfold.

Every account gets `<inbox>+<token>@<domain>`. Mail providers such as Gmail deliver plus
addresses to the main inbox, so one mailbox serves everyone and the token says whose it is.
"""

import email
import hashlib
import html
import imaplib
import re
import secrets
import string
from contextlib import contextmanager
from dataclasses import dataclass, field
from email import policy
from email.utils import getaddresses

IMAP_TIMEOUT_SECONDS = 15
TOKEN_ALPHABET = string.ascii_lowercase + string.digits
TOKEN_LENGTH = 10
MAX_FILES_PER_EMAIL = 5
# Inline pictures smaller than this are logos and icons in a signature, not documents.
MIN_INLINE_IMAGE_BYTES = 20 * 1024
# A body shorter than this has nothing worth reading, such as "see attached".
MIN_BODY_CHARACTERS = 40
MAX_BODY_CHARACTERS = 50_000
RECIPIENT_HEADERS = ("Delivered-To", "X-Original-To", "Envelope-To", "To", "Cc")
GMAIL_DOMAINS = ("gmail.com", "googlemail.com")


def new_token():
    return "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH))


def address_for(inbox, token):
    local, _, domain = inbox.partition("@")
    return f"{local}+{token}@{domain}"


def _local_key(local, domain):
    # Gmail ignores dots and case in the part before the @.
    local = local.lower()
    return local.replace(".", "") if domain in GMAIL_DOMAINS else local


def token_for(recipients, inbox):
    """The token of the first recipient that is a plus address of `inbox`, or None."""
    inbox_local, _, inbox_domain = inbox.lower().partition("@")
    wanted = _local_key(inbox_local, inbox_domain)
    for address in recipients:
        local, _, domain = address.lower().partition("@")
        base, plus, token = local.partition("+")
        if plus and domain == inbox_domain and _local_key(base, domain) == wanted and re.fullmatch(r"[a-z0-9]{6,40}", token):
            return token
    return None


@dataclass
class Parsed:
    key: str
    sender: str
    subject: str
    recipients: list
    files: list = field(default_factory=list)  # (filename, bytes)
    from_body: bool = False


def _text_of_html(markup):
    markup = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h[1-6])>", "\n", markup)
    return html.unescape(re.sub(r"<[^>]+>", " ", markup))


def _body_text(message):
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        text = part.get_content()
    except (LookupError, ValueError):
        return ""
    if part.get_content_type() == "text/html":
        text = _text_of_html(text)
    text = re.sub(r"[ \t]+", " ", text.replace("\r", ""))
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()[:MAX_BODY_CHARACTERS]


def _attachments(message):
    files = []
    for part in message.walk():
        if part.is_multipart() or len(files) >= MAX_FILES_PER_EMAIL:
            continue
        filename = part.get_filename()
        disposition = part.get_content_disposition()
        if not filename or disposition not in ("attachment", "inline", None):
            continue
        data = part.get_payload(decode=True) or b""
        if not data:
            continue
        if disposition != "attachment" and part.get_content_maintype() == "image" and len(data) < MIN_INLINE_IMAGE_BYTES:
            continue
        files.append((filename, data))
    return files


def _safe_name(subject):
    words = re.sub(r"[^A-Za-z0-9]+", "-", subject).strip("-").lower()[:60]
    return f"email-{words or 'message'}.txt"


def parse_message(raw):
    message = email.message_from_bytes(raw, policy=policy.default)
    recipients = []
    for header in RECIPIENT_HEADERS:
        values = [str(value) for value in message.get_all(header, [])]
        recipients += [address for _, address in getaddresses(values) if address]
    # The Message-ID keeps one email from being read twice. A hash stands in if it's missing.
    key = str(message.get("Message-ID") or "").strip() or "sha256:" + hashlib.sha256(raw).hexdigest()
    sender = getaddresses([str(message.get("From", ""))])
    parsed = Parsed(
        key=key[:300],
        sender=(sender[0][1] if sender else "")[:300],
        subject=re.sub(r"\s+", " ", str(message.get("Subject", ""))).strip()[:300],
        recipients=recipients,
        files=_attachments(message),
    )
    if not parsed.files:
        body = _body_text(message)
        if len(body) >= MIN_BODY_CHARACTERS:
            text = f"Subject: {parsed.subject}\nFrom: {parsed.sender}\n\n{body}" if parsed.subject else body
            parsed.files = [(_safe_name(parsed.subject), text.encode("utf-8"))]
            parsed.from_body = True
    return parsed


class ImapMailbox:
    """Reads unread mail over IMAP and marks each message read once it's handled."""

    def __init__(self, host, user, password, port=993):
        self.host, self.user, self.password, self.port = host, user, password, port

    @contextmanager
    def session(self):
        imap = imaplib.IMAP4_SSL(self.host, self.port, timeout=IMAP_TIMEOUT_SECONDS)
        try:
            imap.login(self.user, self.password)
            imap.select("INBOX")
            yield _Session(imap)
        finally:
            try:
                imap.logout()
            except (imaplib.IMAP4.error, OSError):
                pass


class _Session:
    def __init__(self, imap):
        self.imap = imap

    def unread(self, limit):
        """Ids of the oldest unread messages, oldest first."""
        status, data = self.imap.uid("SEARCH", None, "UNSEEN")
        if status != "OK":
            raise imaplib.IMAP4.error("Search failed")
        return (data[0] or b"").split()[:limit]

    def fetch(self, uid):
        # PEEK leaves the message unread until it has been handled.
        status, data = self.imap.uid("FETCH", uid, "(BODY.PEEK[])")
        if status != "OK":
            raise imaplib.IMAP4.error("Fetch failed")
        return next(part[1] for part in data if isinstance(part, tuple))

    def mark_read(self, uid):
        self.imap.uid("STORE", uid, "+FLAGS", "(\\Seen)")
