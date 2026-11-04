"""Checks and cleans extracted fields, and turns them into the record sent to HubSpot."""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import phonenumbers

from .schema import EDITED_SOURCE, FIELDS, REQUIRED_FOR_SYNC

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9-]+\.)+[a-z]{2,}$")
CURRENCY_SYMBOLS = (("PKR", ("PKR", "Rs.", "Rs", "₨")), ("USD", ("US$", "$")), ("EUR", ("€",)), ("GBP", ("£",)))
FREE_MAIL = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "proton.me", "aol.com", "live.com"}
DATE_FORMATS = ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y", "%Y/%m/%d")
CENT = Decimal("0.01")

INVALID = {
    "email": "This doesn't look like a valid email address.",
    "phone": "This doesn't look like a valid phone number.",
    "date": "This doesn't look like a date.",
    "money": "This doesn't look like an amount.",
    "currency": "Use a three-letter currency code, such as USD.",
    "domain": "This doesn't look like a website address.",
}
NO_COUNTRY_CODE = "Add the country code, for example +1 for the US."


class Invalid(Exception):
    def __init__(self, value, message):
        self.value = value
        self.message = message


def _text(raw):
    text = re.sub(r"\s+", " ", str(raw)).strip()
    return text or None


def _email(raw):
    email = raw.strip().removeprefix("mailto:").lower()
    if not EMAIL_RE.match(email):
        raise Invalid(raw.strip(), INVALID["email"])
    return email


def _phone(raw):
    raw = raw.strip()
    if not raw.startswith("+") and not raw.startswith("00"):
        # Guessing a country would put a wrong number into the CRM, so the user decides.
        raise Invalid(raw, NO_COUNTRY_CODE)
    try:
        number = phonenumbers.parse("+" + raw[2:] if raw.startswith("00") else raw, None)
    except phonenumbers.NumberParseException:
        raise Invalid(raw, INVALID["phone"]) from None
    if not phonenumbers.is_valid_number(number):
        raise Invalid(raw, INVALID["phone"])
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def _date(raw, month_first):
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", raw.strip())
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    match = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", text)
    if match:
        first, second, year = (int(part) for part in match.groups())
        # 03/04/2026 could be either order. US documents put the month first; most others don't.
        if first > 12 or (second <= 12 and not month_first):
            day, month = first, second
        else:
            month, day = first, second
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            pass
    raise Invalid(raw.strip(), INVALID["date"])


def _decimal(raw):
    text = str(raw).strip()
    for _, symbols in CURRENCY_SYMBOLS:
        for symbol in symbols:
            text = text.replace(symbol, "")
    text = re.sub(r"[A-Za-z\s,]", "", text)
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _money(raw):
    amount = _decimal(raw)
    if amount is None or not amount.is_finite():
        raise Invalid(str(raw).strip(), INVALID["money"])
    return str(amount.quantize(CENT))


def _quantity(raw):
    amount = _decimal(raw)
    if amount is None or not amount.is_finite():
        return _text(raw)
    return format(amount.normalize(), "f")


def _has_symbol(text, symbol):
    if not symbol[0].isalpha():
        return symbol in text
    # "Rs" must stand alone, or "Dollars" would read as rupees.
    return re.search(r"(?<![A-Za-z])" + re.escape(symbol) + r"(?![A-Za-z])", text, re.IGNORECASE) is not None


def currency_from_text(text):
    text = str(text).strip()
    for code, symbols in CURRENCY_SYMBOLS:
        if any(_has_symbol(text, symbol) for symbol in symbols):
            return code
    return None


def _currency(raw):
    text = raw.strip().upper()
    if re.fullmatch(r"[A-Z]{3}", text):
        return text
    code = currency_from_text(raw)
    if code is None:
        raise Invalid(raw.strip(), INVALID["currency"])
    return code


def _domain(raw):
    host = raw.strip().lower()
    if "@" in host:
        host = host.rsplit("@", 1)[1]
    host = re.sub(r"^[a-z]+://", "", host).split("/")[0].split(":")[0].removeprefix("www.")
    if not DOMAIN_RE.match(host):
        raise Invalid(raw.strip(), INVALID["domain"])
    return host


def _line_items(raw):
    items = []
    for item in raw if isinstance(raw, list) else []:
        description = _text(item.get("description") or "")
        if not description:
            continue
        cleaned = {"description": description, "quantity": None, "unit_price": None, "amount": None}
        if item.get("quantity") not in (None, ""):
            cleaned["quantity"] = _quantity(item["quantity"])
        for key in ("unit_price", "amount"):
            if item.get(key) not in (None, ""):
                try:
                    cleaned[key] = _money(item[key])
                except Invalid:
                    cleaned[key] = None
        items.append(cleaned)
    return items or None


def _clean(kind, raw, month_first):
    """Returns (value, issue). An invalid value is kept as written so the user can fix it."""
    if kind == "line_items":
        return _line_items(raw), None
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, None
    try:
        if kind == "email":
            return _email(str(raw)), None
        if kind == "phone":
            return _phone(str(raw)), None
        if kind == "date":
            return _date(str(raw), month_first), None
        if kind == "money":
            return _money(raw), None
        if kind == "currency":
            return _currency(str(raw)), None
        if kind == "domain":
            return _domain(str(raw)), None
    except Invalid as invalid:
        return invalid.value, invalid.message
    return _text(raw), None


def _confidence(value):
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _format_amount(value):
    return f"{Decimal(value):,.2f}"


def normalize_fields(raw, edited_keys=frozenset()):
    """Turns raw values (from Gemini or the user) into the stored shape, with any problems listed."""
    entries = {key: dict(raw.get(key) or {}) for key in FIELDS}

    # Fill a missing currency from the symbol on the total, such as "$1,250.00".
    currency, total = entries["invoice.currency"], entries["invoice.total"]
    if not currency.get("value") and total.get("value"):
        code = currency_from_text(str(total["value"]))
        if code:
            currency.update(value=code, confidence=total.get("confidence"), source=total.get("source", ""))
    month_first = (_clean("currency", currency.get("value"), False)[0] or "") == "USD"

    fields = {}
    for key, spec in FIELDS.items():
        entry = entries[key]
        edited = key in edited_keys or bool(entry.get("edited"))
        value, issue = _clean(spec["type"], entry.get("value"), month_first)
        fields[key] = {
            "value": value,
            "confidence": 1.0 if edited else (_confidence(entry.get("confidence")) if value is not None else 0.0),
            "source": EDITED_SOURCE if edited else str(entry.get("source") or ""),
            "issues": [issue] if issue else [],
            "edited": edited,
        }
    _check_invoice(fields)
    return fields


def _check_invoice(fields):
    total, items = fields["invoice.total"], fields["invoice.line_items"]["value"]
    if total["value"] and not total["issues"] and items and all(item["amount"] for item in items):
        added = sum(Decimal(item["amount"]) for item in items)
        if abs(added - Decimal(total["value"])) > CENT:
            total["issues"].append(
                f"Line items add up to {_format_amount(added)} but the total says {_format_amount(total['value'])}."
            )
    issued, due = fields["invoice.issue_date"], fields["invoice.due_date"]
    if issued["value"] and due["value"] and not issued["issues"] and not due["issues"]:
        if due["value"] < issued["value"]:
            due["issues"].append("The due date is before the issue date.")


def apply_edits(fields, edits):
    """Applies the user's changes, then checks every field again so cross-field problems update too."""
    unknown = [key for key in edits if key not in FIELDS]
    if unknown:
        raise ValueError(unknown[0])
    raw = {key: dict(field) for key, field in fields.items()}
    for key, value in edits.items():
        raw[key] = {"value": value, "confidence": 1.0, "source": EDITED_SOURCE, "edited": True}
    return normalize_fields(raw)


def missing_for_sync(fields):
    return [key for key in REQUIRED_FOR_SYNC if fields[key]["value"] is None or fields[key]["issues"]]


def _value(fields, key):
    return fields[key]["value"]


def _line_items_summary(items, currency):
    lines = []
    for item in items:
        parts = [item["description"]]
        if item["quantity"]:
            parts.append(f"x {item['quantity']}")
        if item["amount"]:
            parts.append(f"{currency + ' ' if currency else ''}{_format_amount(item['amount'])}")
        lines.append("- " + ", ".join(parts))
    return "Line items:\n" + "\n".join(lines)


def to_crm_record(fields, doc_type):
    """The one payload both the direct HubSpot client and the n8n workflow send."""
    email = _value(fields, "contact.email")
    contact = {"email": email}
    for key, prop in (
        ("contact.first_name", "firstname"),
        ("contact.last_name", "lastname"),
        ("contact.phone", "phone"),
        ("contact.job_title", "jobtitle"),
    ):
        if _value(fields, key) is not None:
            contact[prop] = _value(fields, key)

    name, domain, address = (_value(fields, k) for k in ("company.name", "company.domain", "company.address"))
    if fields["company.domain"]["issues"]:
        domain = None
    if not domain and email and "@" in email:
        email_domain = email.rsplit("@", 1)[1]
        if email_domain not in FREE_MAIL:
            domain = email_domain
    company = None
    if name or domain:
        company = {k: v for k, v in (("name", name), ("domain", domain), ("address", address)) if v}

    currency = _value(fields, "invoice.currency")
    deal = None
    total = _value(fields, "invoice.total")
    if doc_type == "invoice" and total and not fields["invoice.total"]["issues"]:
        number = _value(fields, "invoice.number")
        title = "Invoice " + number if number else "Invoice"
        if name:
            title += " — " + name
        deal = {"dealname": title, "amount": total}
        if currency:
            deal["currency"] = currency
        closedate = _value(fields, "invoice.due_date") or _value(fields, "invoice.issue_date")
        if closedate and not fields["invoice.due_date"]["issues"]:
            deal["closedate"] = closedate
        if number:
            deal["invoice_number"] = number

    note_parts = []
    if _value(fields, "notes"):
        note_parts.append(_value(fields, "notes"))
    if doc_type == "invoice" and _value(fields, "invoice.line_items"):
        note_parts.append(_line_items_summary(_value(fields, "invoice.line_items"), currency))
    note = "\n\n".join(note_parts) or None

    return {"contact": contact, "company": company, "deal": deal, "note": note}
