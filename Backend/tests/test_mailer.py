from app import mailer
from app.mailer import named_sender, render_html, smtp_sender


def test_email_html_escapes_everything_it_is_given():
    html = render_html("<b>Hi</b>", "a & b", "Go", 'https://x.test/?a=1&b="2"', "Note:", "<script>", "https://x.test/logo.png")
    assert "<b>Hi</b>" not in html and "&lt;b&gt;Hi&lt;/b&gt;" in html
    assert "<script>" not in html
    assert 'href="https://x.test/?a=1&amp;b=&quot;2&quot;"' in html


def test_sender_always_carries_a_name():
    assert named_sender("hello@inkfold.test") == "Inkfold <hello@inkfold.test>"
    assert named_sender("Ahmad <hello@inkfold.test>") == "Ahmad <hello@inkfold.test>"


def test_smtp_sender_uses_starttls_and_sends_both_parts(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            calls.append(("starttls",))

        def login(self, user, password):
            calls.append(("login", user))

        def send_message(self, message):
            calls.append(("send", message))

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    send = smtp_sender("smtp.test", 587, "me@inkfold.test", "app-password", "me@inkfold.test", "https://app.test/email-logo.png")
    send("sara@example.com", "Reset", "Reset your password", "Text", "Choose", "https://app.test/r?token=t", "Lead", "Note")
    assert [c[0] for c in calls] == ["connect", "starttls", "login", "send"]
    message = calls[-1][1]
    assert message["To"] == "sara@example.com" and message["From"] == "Inkfold <me@inkfold.test>"
    assert message["X-Entity-Ref-ID"]
    plain = message.get_body(("plain",)).get_content()
    html = message.get_body(("html",)).get_content()
    assert "https://app.test/r?token=t" in plain and "https://app.test/email-logo.png" in html
