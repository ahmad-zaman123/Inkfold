from email.message import EmailMessage

import psycopg
import pytest
from google.genai import errors

from app.intake import address_for, parse_message, token_for
from conftest import FakeInbox, make_settings, sign_in

INBOX = "inkfold.intake@gmail.com"
INTAKE = {"inbox": INBOX, "host": "imap.test", "user": INBOX, "password": "app-password"}
PDF = b"%PDF-1.4 invoice bytes"
PNG = b"\x89PNG\r\n\x1a\n" + b"card" * 10_000
BUSY = errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})
LEAD = "Hi, Lina Haddad from Cedar & Co here. Please call me on 0412 555 019 about the desks for our new office."


def email_to(to, subject="Invoice INV-1042", attachments=(), body="Please see attached.", message_id=None, html=None):
    message = EmailMessage()
    message["From"] = "Sara Malik <sara@example.com>"
    message["To"] = to
    message["Subject"] = subject
    if message_id:
        message["Message-ID"] = message_id
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")
    for name, data, maintype, subtype in attachments:
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return message.as_bytes()


def pdf(name="invoice.pdf", data=PDF):
    return (name, data, "application", "pdf")


@pytest.fixture
def inbox():
    return FakeInbox()


@pytest.fixture
def intake_client(build, inbox):
    return build(mailbox=inbox, intake=INTAKE, intake_poll_secret="poll-secret")


def my_address(client, auth):
    view = client.get("/api/intake", headers=auth).json()
    assert view["enabled"] is True
    return view["address"]


def check(client, auth):
    response = client.post("/api/intake/check", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def documents(client, auth):
    return client.get("/api/documents", headers=auth).json()["documents"]


# Reading emails


def test_plus_addresses_find_the_token_and_ignore_everything_else():
    assert token_for(["someone@example.com", "Inkfold.Intake+abc123xyz9@gmail.com"], INBOX) == "abc123xyz9"
    # Gmail ignores dots before the @.
    assert token_for(["inkfoldintake+abc123xyz9@googlemail.com"], "inkfold.intake@googlemail.com") == "abc123xyz9"
    assert token_for(["inkfold.intake@gmail.com"], INBOX) is None
    assert token_for(["other+abc123xyz9@gmail.com"], INBOX) is None
    assert token_for(["inkfold.intake+abc123xyz9@example.com"], INBOX) is None
    assert token_for(["inkfold.intake+a'b@gmail.com"], INBOX) is None
    assert address_for(INBOX, "abc123xyz9") == "inkfold.intake+abc123xyz9@gmail.com"


def test_attachments_are_read_and_signature_logos_are_skipped():
    raw = email_to(address_for(INBOX, "abc123xyz9"), attachments=[pdf(), ("card.png", PNG, "image", "png")])
    message = EmailMessage()
    message["To"] = address_for(INBOX, "abc123xyz9")
    message.set_content("Thanks")
    message.add_related(b"\x89PNG\r\n\x1a\n" + b"x" * 500, maintype="image", subtype="png", filename="logo.png", disposition="inline")
    parsed = parse_message(raw)
    assert [name for name, _ in parsed.files] == ["invoice.pdf", "card.png"] and not parsed.from_body
    assert parsed.sender == "sara@example.com" and parsed.subject == "Invoice INV-1042"
    assert parse_message(message.as_bytes()).files == []


def test_an_email_without_attachments_is_read_from_its_text():
    parsed = parse_message(email_to(INBOX, subject="New lead: Cedar & Co", body=LEAD))
    [(name, data)] = parsed.files
    assert parsed.from_body and name == "email-new-lead-cedar-co.txt"
    text = data.decode()
    assert text.startswith("Subject: New lead: Cedar & Co\nFrom: sara@example.com") and "0412 555 019" in text


def test_html_only_bodies_become_plain_text():
    message = EmailMessage()
    message["To"] = INBOX
    message["Subject"] = "Lead"
    message.set_content(f"<html><style>p{{color:red}}</style><p>{LEAD.replace('&', '&amp;')}</p><script>x()</script></html>", subtype="html")
    [(_, data)] = parse_message(message.as_bytes()).files
    assert "Cedar & Co" in data.decode() and "color:red" not in data.decode() and "x()" not in data.decode()


def test_a_short_note_without_attachments_has_nothing_to_read():
    assert parse_message(email_to(INBOX, body="See attached, thanks!")).files == []


def test_emails_without_a_message_id_still_get_a_stable_key():
    raw = email_to(INBOX)
    assert parse_message(raw).key == parse_message(raw).key and parse_message(raw).key.startswith("sha256:")


# The API


def test_intake_is_hidden_without_an_inbox(client, auth):
    assert client.get("/api/intake", headers=auth).json() == {"enabled": False}
    assert client.post("/api/intake/check", headers=auth).status_code == 404
    assert client.post("/api/intake/poll", headers={"Authorization": "Bearer x"}).status_code == 404


def test_each_account_has_its_own_lasting_address(intake_client):
    sara, omar = sign_in(intake_client), sign_in(intake_client, "omar@example.com")
    address = my_address(intake_client, sara)
    assert address == my_address(intake_client, sara) != my_address(intake_client, omar)
    assert intake_client.get("/api/intake", headers=sara).json()["scheduled"] is True
    assert token_for([address], INBOX) is not None


def test_a_forwarded_invoice_lands_ready_for_review(intake_client, inbox, auth, gemini):
    uid = inbox.add(email_to(my_address(intake_client, auth), attachments=[pdf()], message_id="<m1@example.com>"))
    result = check(intake_client, auth)
    assert result["handled"] == 1 and inbox.messages[uid][1] is True
    [document] = documents(intake_client, auth)
    assert document["filename"] == "invoice.pdf" and document["status"] == "needs_review"
    assert document["source"] == {"via": "email", "from": "sara@example.com", "subject": "Invoice INV-1042"}
    [recent] = result["recent"]
    assert recent["status"] == "added" and recent["detail"] == "Added 1 document." and recent["document_ids"] == [document["id"]]
    assert gemini.calls == 1


def test_a_lead_email_with_no_attachment_is_read_from_its_body(intake_client, inbox, auth):
    inbox.add(email_to(my_address(intake_client, auth), subject="New lead", body=LEAD))
    check(intake_client, auth)
    [document] = documents(intake_client, auth)
    assert document["mime"] == "text/plain" and document["filename"] == "email-new-lead.txt"


def test_each_email_is_read_once_even_if_it_is_still_unread(intake_client, inbox, auth, gemini):
    raw = email_to(my_address(intake_client, auth), attachments=[pdf()], message_id="<same@example.com>")
    inbox.add(raw)
    check(intake_client, auth)
    # The same email again, say after the inbox lost its read mark.
    inbox.add(raw)
    assert check(intake_client, auth)["handled"] == 1
    assert len(documents(intake_client, auth)) == 1 and gemini.calls == 1 and inbox.unread_count() == 0


def test_one_email_sent_to_two_accounts_reaches_both(intake_client, inbox, gemini):
    sara, omar = sign_in(intake_client), sign_in(intake_client, "omar@example.com")
    for auth in (sara, omar):
        inbox.add(email_to(my_address(intake_client, auth), attachments=[pdf()], message_id="<shared@example.com>"))
    check(intake_client, sara)
    assert len(documents(intake_client, sara)) == 1 and len(documents(intake_client, omar)) == 1


def test_the_same_file_in_a_new_email_is_spotted_as_a_duplicate(intake_client, inbox, auth, gemini):
    address = my_address(intake_client, auth)
    inbox.add(email_to(address, attachments=[pdf()], message_id="<a@example.com>"))
    inbox.add(email_to(address, attachments=[pdf()], message_id="<b@example.com>"))
    recent = check(intake_client, auth)["recent"]
    assert [r["status"] for r in recent] == ["duplicate", "added"] and recent[0]["detail"] == "Already in Inkfold."
    assert gemini.calls == 1


def test_mail_to_unknown_addresses_is_ignored_and_marked_read(intake_client, inbox, auth, gemini):
    inbox.add(email_to(INBOX, attachments=[pdf()]))
    inbox.add(email_to(address_for(INBOX, "unknown12345"), attachments=[pdf()]))
    assert check(intake_client, auth)["handled"] == 2
    assert inbox.unread_count() == 0 and gemini.calls == 0 and documents(intake_client, auth) == []
    assert check(intake_client, auth)["recent"] == []


def test_mail_only_reaches_the_account_it_was_sent_to(intake_client, inbox):
    sara, omar = sign_in(intake_client), sign_in(intake_client, "omar@example.com")
    inbox.add(email_to(my_address(intake_client, omar), attachments=[pdf()]))
    check(intake_client, sara)
    assert documents(intake_client, sara) == [] and len(documents(intake_client, omar)) == 1
    assert intake_client.get("/api/intake", headers=sara).json()["recent"] == []


def test_unsupported_attachments_are_skipped_and_explained(intake_client, inbox, auth):
    inbox.add(email_to(my_address(intake_client, auth), attachments=[("notes.docx", b"PK\x03\x04docx", "application", "octet-stream")]))
    [recent] = check(intake_client, auth)["recent"]
    assert recent["status"] == "skipped" and recent["detail"] == "No PDF, PNG, JPG or text to read in this email."
    assert inbox.unread_count() == 0


def test_a_new_address_stops_the_old_one(intake_client, inbox, auth, gemini):
    old = my_address(intake_client, auth)
    new = intake_client.post("/api/intake/address", headers=auth).json()["address"]
    assert new != old and my_address(intake_client, auth) == new
    inbox.add(email_to(old, attachments=[pdf()]))
    check(intake_client, auth)
    assert documents(intake_client, auth) == [] and gemini.calls == 0


def test_one_check_reads_a_few_documents_and_leaves_the_rest(intake_client, inbox, auth, gemini):
    address = my_address(intake_client, auth)
    for n in range(7):
        inbox.add(email_to(address, attachments=[pdf(f"invoice-{n}.pdf", PDF + bytes([n]))], message_id=f"<m{n}@example.com>"))
    assert check(intake_client, auth)["handled"] == 5
    assert inbox.unread_count() == 2 and gemini.calls == 5
    assert check(intake_client, auth)["handled"] == 2
    assert len(documents(intake_client, auth)) == 7


def test_an_email_bigger_than_whats_left_waits_for_the_next_check(intake_client, inbox, auth, gemini):
    address = my_address(intake_client, auth)
    inbox.add(email_to(address, attachments=[pdf("one.pdf", PDF + b"1")], message_id="<one@example.com>"))
    files = [pdf(f"{n}.pdf", PDF + bytes([n])) for n in range(5)]
    inbox.add(email_to(address, attachments=files, message_id="<five@example.com>"))
    assert check(intake_client, auth)["handled"] == 1
    assert inbox.unread_count() == 1
    assert check(intake_client, auth)["handled"] == 1 and len(documents(intake_client, auth)) == 6


def test_when_gemini_is_busy_the_email_is_tried_again_later(intake_client, inbox, auth, gemini):
    gemini.replies = [BUSY] * 8
    inbox.add(email_to(my_address(intake_client, auth), attachments=[pdf()], message_id="<busy@example.com>"))
    assert intake_client.post("/api/intake/check", headers=auth).status_code == 503
    assert inbox.unread_count() == 1
    gemini.replies = []
    assert check(intake_client, auth)["handled"] == 1 and len(documents(intake_client, auth)) == 1


def test_the_daily_upload_limit_applies_to_email_too(build, inbox):
    client = build(mailbox=inbox, intake=INTAKE, uploads_per_user_day=1)
    auth = sign_in(client)
    address = my_address(client, auth)
    inbox.add(email_to(address, attachments=[pdf("a.pdf", PDF + b"a"), pdf("b.pdf", PDF + b"b")]))
    [recent] = check(client, auth)["recent"]
    assert recent["status"] == "added"
    assert recent["detail"] == "Added 1 document. You've reached today's upload limit. Please try again tomorrow."
    assert inbox.unread_count() == 0


def test_an_unreachable_inbox_is_reported_plainly(intake_client, inbox, auth):
    inbox.down = True
    response = intake_client.post("/api/intake/check", headers=auth)
    assert response.status_code == 503 and response.json()["detail"].startswith("Couldn't reach the intake inbox")


def test_checking_by_hand_is_rate_limited(intake_client, auth):
    codes = [intake_client.post("/api/intake/check", headers=auth).status_code for _ in range(7)]
    assert codes[:6] == [200] * 6 and codes[6] == 429


def test_the_scheduled_check_needs_the_secret(intake_client, inbox, auth, build):
    inbox.add(email_to(my_address(intake_client, auth), attachments=[pdf()]))
    assert intake_client.post("/api/intake/poll").status_code == 401
    assert intake_client.post("/api/intake/poll", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert intake_client.post("/api/intake/poll", headers={"Authorization": "Bearer poll-secret"}).json() == {"handled": 1}
    # Without a secret set, the scheduled check is off and only the button works.
    no_secret = build(mailbox=inbox, intake=INTAKE)
    assert no_secret.get("/api/intake", headers=auth).json()["scheduled"] is False
    assert no_secret.post("/api/intake/poll", headers={"Authorization": "Bearer "}).status_code == 404


def test_a_check_that_died_part_way_is_picked_up_again(intake_client, inbox, auth, store, database_url):
    raw = email_to(my_address(intake_client, auth), attachments=[pdf()], message_id="<stuck@example.com>")
    inbox.add(raw)
    token = token_for([my_address(intake_client, auth)], INBOX)
    assert store.claim_intake_message(f"{token}:<stuck@example.com>") == "claimed"
    # Another check has it, so it's left alone for now.
    assert check(intake_client, auth)["handled"] == 0 and inbox.unread_count() == 1
    with psycopg.connect(database_url, autocommit=True) as db:
        db.execute("UPDATE intake_messages SET created_at = now() - interval '11 minutes'")
    assert check(intake_client, auth)["handled"] == 1 and len(documents(intake_client, auth)) == 1


def test_demo_accounts_get_an_address_too(intake_client, inbox):
    session = intake_client.post("/api/auth/demo").json()
    auth = {"Authorization": "Bearer " + session["token"]}
    inbox.add(email_to(my_address(intake_client, auth), subject="Lead", body=LEAD))
    check(intake_client, auth)
    assert len(documents(intake_client, auth)) == 4


def test_settings_turn_intake_on(monkeypatch):
    from app.config import load_settings

    for name, value in {"DATABASE_URL": "x", "JWT_SECRET": "y", "GEMINI_API_KEY": "z"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("INTAKE_EMAIL", raising=False)
    assert load_settings()["intake"] is None
    monkeypatch.setenv("INTAKE_EMAIL", INBOX)
    monkeypatch.setenv("INTAKE_PASSWORD", "app-password")
    assert load_settings()["intake"] == {"inbox": INBOX, "host": "imap.gmail.com", "user": INBOX, "password": "app-password"}
    assert make_settings("")["intake"] is None


def test_the_imap_mailbox_peeks_then_marks_read(monkeypatch):
    from app import intake

    calls = []

    class FakeIMAP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port))

        def login(self, user, password):
            calls.append(("login", user))

        def select(self, folder):
            calls.append(("select", folder))

        def uid(self, command, *args):
            calls.append((command, *args))
            if command == "SEARCH":
                return "OK", [b"7 9"]
            if command == "FETCH":
                return "OK", [(b"7 (UID 7 BODY[] {5}", b"raw-7"), b")"]
            return "OK", [None]

        def logout(self):
            calls.append(("logout",))

    monkeypatch.setattr(intake.imaplib, "IMAP4_SSL", FakeIMAP)
    with intake.ImapMailbox("imap.test", INBOX, "app-password").session() as session:
        assert session.unread(limit=1) == [b"7"]
        assert session.fetch(b"7") == b"raw-7"
        session.mark_read(b"7")
    assert calls == [
        ("connect", "imap.test", 993), ("login", INBOX), ("select", "INBOX"),
        ("SEARCH", None, "UNSEEN"), ("FETCH", b"7", "(BODY.PEEK[])"), ("STORE", b"7", "+FLAGS", "(\\Seen)"), ("logout",),
    ]
