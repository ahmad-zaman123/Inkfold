import pytest

from app.normalize import NO_COUNTRY_CODE, apply_edits, missing_for_sync, normalize_fields, to_crm_record
from app.schema import EDITED_SOURCE, FIELDS


def raw(**values):
    return {key.replace("__", "."): {"value": value, "confidence": 0.9, "source": str(value)} for key, value in values.items()}


def value(fields, key):
    return fields[key]["value"]


def issues(fields, key):
    return fields[key]["issues"]


def test_every_field_is_present_and_empty_ones_have_no_confidence():
    fields = normalize_fields({})
    assert list(fields) == list(FIELDS)
    assert fields["contact.email"] == {"value": None, "confidence": 0.0, "source": "", "issues": [], "edited": False}


def test_email_is_lowercased_and_checked():
    assert value(normalize_fields(raw(contact__email=" Sara@Brightpath.Example ")), "contact.email") == "sara@brightpath.example"
    assert value(normalize_fields(raw(contact__email="mailto:a@b.co")), "contact.email") == "a@b.co"
    bad = normalize_fields(raw(contact__email="sara at brightpath"))
    assert value(bad, "contact.email") == "sara at brightpath"
    assert issues(bad, "contact.email") == ["This doesn't look like a valid email address."]


@pytest.mark.parametrize(
    "written, expected",
    [("+1 (415) 555-0132", "+14155550132"), ("+92 300 1234567", "+923001234567"), ("0044 20 7946 0958", "+442079460958")],
)
def test_phone_with_country_code_becomes_e164(written, expected):
    fields = normalize_fields(raw(contact__phone=written))
    assert value(fields, "contact.phone") == expected
    assert issues(fields, "contact.phone") == []


def test_phone_without_country_code_is_kept_and_flagged():
    fields = normalize_fields(raw(contact__phone="(415) 555-0132"))
    assert value(fields, "contact.phone") == "(415) 555-0132"
    assert issues(fields, "contact.phone") == [NO_COUNTRY_CODE]


def test_invalid_phone_is_flagged():
    assert issues(normalize_fields(raw(contact__phone="+1 12")), "contact.phone") == ["This doesn't look like a valid phone number."]


@pytest.mark.parametrize(
    "written, currency, expected",
    [
        ("2026-11-02", None, "2026-11-02"),
        ("2 November 2026", None, "2026-11-02"),
        ("Nov 2, 2026", None, "2026-11-02"),
        ("2nd November 2026", None, "2026-11-02"),
        ("25/11/2026", None, "2026-11-25"),
        ("11/25/2026", None, "2026-11-25"),
        ("03/04/2026", "USD", "2026-03-04"),
        ("03/04/2026", "EUR", "2026-04-03"),
        ("03.04.2026", None, "2026-04-03"),
    ],
)
def test_dates(written, currency, expected):
    values = {"invoice__issue_date": written}
    if currency:
        values["invoice__currency"] = currency
    assert value(normalize_fields(raw(**values)), "invoice.issue_date") == expected


def test_bad_date_is_flagged():
    fields = normalize_fields(raw(invoice__issue_date="31/02/2026"))
    assert issues(fields, "invoice.issue_date") == ["This doesn't look like a date."]


@pytest.mark.parametrize("written, expected", [("$1,250", "1250.00"), ("1250.5", "1250.50"), ("Rs. 45,000", "45000.00"), ("€ 99.999", "100.00")])
def test_money(written, expected):
    assert value(normalize_fields(raw(invoice__total=written)), "invoice.total") == expected


def test_money_that_isnt_a_number_is_flagged():
    assert issues(normalize_fields(raw(invoice__total="twelve")), "invoice.total") == ["This doesn't look like an amount."]


def test_currency_from_code_or_symbol():
    assert value(normalize_fields(raw(invoice__currency="usd")), "invoice.currency") == "USD"
    assert value(normalize_fields(raw(invoice__currency="£")), "invoice.currency") == "GBP"
    assert value(normalize_fields(raw(invoice__currency="Rs")), "invoice.currency") == "PKR"
    assert issues(normalize_fields(raw(invoice__currency="dollars")), "invoice.currency")
    assert value(normalize_fields(raw(invoice__total="Dollars 40")), "invoice.currency") is None


def test_missing_currency_is_taken_from_the_total():
    fields = normalize_fields(raw(invoice__total="$1,250.00"))
    assert value(fields, "invoice.currency") == "USD"
    assert fields["invoice.currency"]["source"] == "$1,250.00"


@pytest.mark.parametrize(
    "written, expected",
    [("https://www.Kestrel-Logistics.example/about", "kestrel-logistics.example"), ("brightpath.example", "brightpath.example"), ("www.cedar.co.example:8080", "cedar.co.example")],
)
def test_domains(written, expected):
    assert value(normalize_fields(raw(company__domain=written)), "company.domain") == expected


def test_bad_domain_is_flagged():
    assert issues(normalize_fields(raw(company__domain="not a site")), "company.domain")


def line_items(*amounts):
    return [{"description": f"Item {i}", "quantity": "1", "unit_price": a, "amount": a} for i, a in enumerate(amounts)]


def test_line_items_are_cleaned_and_blank_ones_dropped():
    fields = normalize_fields(
        raw(invoice__line_items=[{"description": " Desk ", "quantity": "2.0", "unit_price": "$400", "amount": "800"}, {"description": ""}])
    )
    assert value(fields, "invoice.line_items") == [{"description": "Desk", "quantity": "2", "unit_price": "400.00", "amount": "800.00"}]


def test_line_items_that_dont_add_up_flag_the_total():
    fields = normalize_fields(raw(invoice__total="1250", invoice__line_items=line_items("800", "400")))
    assert issues(fields, "invoice.total") == ["Line items add up to 1,200.00 but the total says 1,250.00."]
    assert issues(normalize_fields(raw(invoice__total="1200", invoice__line_items=line_items("800", "400"))), "invoice.total") == []


def test_due_date_before_issue_date_is_flagged():
    fields = normalize_fields(raw(invoice__issue_date="2026-11-10", invoice__due_date="2026-11-01"))
    assert issues(fields, "invoice.due_date") == ["The due date is before the issue date."]


def test_confidence_is_clamped():
    fields = normalize_fields({"notes": {"value": "x", "confidence": 7, "source": "x"}, "company.name": {"value": "y", "confidence": "?"}})
    assert fields["notes"]["confidence"] == 1.0
    assert fields["company.name"]["confidence"] == 0.0


def test_normalizing_twice_changes_nothing():
    once = normalize_fields(raw(contact__phone="+1 415 555 0132", invoice__issue_date="03/04/2026", invoice__total="$9", company__domain="https://x.example"))
    assert normalize_fields(once) == once


def test_edits_mark_the_field_and_recheck_everything():
    fields = normalize_fields(raw(contact__phone="(415) 555-0132", invoice__total="1250", invoice__line_items=line_items("800", "400")))
    fields = apply_edits(fields, {"contact.phone": "+1 415 555 0132", "invoice.total": "1200"})
    assert fields["contact.phone"] == {"value": "+14155550132", "confidence": 1.0, "source": EDITED_SOURCE, "issues": [], "edited": True}
    assert issues(fields, "invoice.total") == []
    assert fields["company.name"]["edited"] is False


def test_clearing_a_field_and_unknown_keys():
    fields = apply_edits(normalize_fields(raw(notes="hello")), {"notes": ""})
    assert value(fields, "notes") is None
    with pytest.raises(ValueError):
        apply_edits(fields, {"contact.fax": "1"})


def test_missing_for_sync_needs_a_valid_email():
    assert missing_for_sync(normalize_fields({})) == ["contact.email"]
    assert missing_for_sync(normalize_fields(raw(contact__email="nope"))) == ["contact.email"]
    assert missing_for_sync(normalize_fields(raw(contact__email="a@b.co"))) == []


def invoice_fields():
    return normalize_fields(
        raw(
            contact__email="sara@brightpath.example",
            contact__first_name="Sara",
            contact__last_name="Malik",
            company__name="Brightpath Studio",
            invoice__number="INV-1042",
            invoice__issue_date="2026-11-02",
            invoice__due_date="2026-12-02",
            invoice__total="$1,200.00",
            invoice__line_items=line_items("800", "400"),
            notes="Net 30",
        )
    )


def test_crm_record_for_an_invoice():
    record = to_crm_record(invoice_fields(), "invoice")
    assert record["contact"] == {"email": "sara@brightpath.example", "firstname": "Sara", "lastname": "Malik"}
    # No website on the invoice, so the work email's domain stands in.
    assert record["company"] == {"name": "Brightpath Studio", "domain": "brightpath.example"}
    assert record["deal"] == {
        "dealname": "Invoice INV-1042 — Brightpath Studio",
        "amount": "1200.00",
        "currency": "USD",
        "closedate": "2026-12-02",
        "invoice_number": "INV-1042",
    }
    assert record["note"].startswith("Net 30\n\nLine items:\n- Item 0, x 1, USD 800.00")


def test_crm_record_without_company_or_deal():
    fields = normalize_fields(raw(contact__email="omar@gmail.com"))
    record = to_crm_record(fields, "business_card")
    assert record == {"contact": {"email": "omar@gmail.com"}, "company": None, "deal": None, "note": None}


def test_no_deal_when_the_total_has_a_problem():
    fields = apply_edits(invoice_fields(), {"invoice.total": "1250"})
    assert to_crm_record(fields, "invoice")["deal"] is None


def test_a_purchase_order_becomes_a_deal_named_after_the_po():
    fields = normalize_fields(raw(contact__email="buyer@acme.example", company__name="Acme", invoice__number="PO-4512",
                                  invoice__total="$2,400.00", invoice__due_date="2026-12-01"))
    deal = to_crm_record(fields, "purchase_order")["deal"]
    assert deal["dealname"] == "PO PO-4512 — Acme" and deal["amount"] == "2400.00" and deal["closedate"] == "2026-12-01"


def test_a_receipt_records_the_payment_in_the_note_not_a_deal():
    fields = normalize_fields(raw(contact__email="shop@cafe.example", company__name="Corner Cafe", invoice__number="8812",
                                  invoice__total="€18.40", invoice__issue_date="2026-11-03", invoice__payment_method="Visa ending 4242"))
    record = to_crm_record(fields, "receipt")
    assert record["deal"] is None
    assert record["note"].startswith("Receipt 8812: EUR 18.40 paid 2026-11-03 by Visa ending 4242")


def test_a_resume_keeps_location_linkedin_and_skills_in_the_note():
    fields = normalize_fields(raw(contact__first_name="Ayesha", contact__email="ayesha@mail.example", contact__job_title="Data engineer",
                                  contact__location="Lahore, Pakistan", contact__linkedin="linkedin.com/in/ayesha",
                                  resume__skills="Python, Airflow, dbt"))
    record = to_crm_record(fields, "resume")
    assert record["contact"]["jobtitle"] == "Data engineer" and record["deal"] is None
    assert record["note"] == "Location: Lahore, Pakistan\nLinkedIn: linkedin.com/in/ayesha\nSkills: Python, Airflow, dbt"


def test_documents_saved_before_a_field_existed_still_make_a_record():
    fields = normalize_fields(raw(contact__email="old@x.example"))
    for key in ("contact.location", "contact.linkedin", "invoice.payment_method", "resume.skills"):
        del fields[key]
    assert to_crm_record(fields, "invoice")["contact"] == {"email": "old@x.example"}
