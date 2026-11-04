"""Builds the accuracy test set: made-up documents in evals/cases/ and their answers in evals/expected.json.

Every person, company and number here is fictional, and every email and website is under example.com.
Rendering uses headless Chrome:  python evals/make_cases.py
"""

import html
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES = HERE / "cases"
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or "google-chrome"
FIELD_KEYS = (
    "contact.first_name", "contact.last_name", "contact.email", "contact.phone", "contact.job_title",
    "company.name", "company.domain", "company.address",
    "invoice.number", "invoice.issue_date", "invoice.due_date", "invoice.currency", "invoice.total",
)
E = html.escape


# Invoices. `customer` is who's billed, which is who Inkfold treats as the contact.

INVOICES = [
    {
        "id": "invoice-01", "layout": "modern", "seller": "Copperleaf Supplies", "seller_lines": ["901 Congress Ave", "Austin, TX 78701", "billing@copperleaf.example.com"],
        "number": "INV-2207", "issue": "10/14/2026", "due": "11/13/2026", "symbol": "$", "code": "USD",
        "customer": ["Daniel Okafor", "Procurement Lead", "Lumen Dental Group", "77 Pine Avenue", "Austin, TX 78702", "daniel.okafor@lumendental.example.com", "+1 512 555 0192"],
        "items": [("Nitrile gloves, case of 1,000", "6", "84.00", "504.00"), ("Autoclave pouches", "20", "38.50", "770.00"), ("Dental bibs, 500 pack", "12", "22.75", "273.00"), ("Sales tax 8.25%", "", "", "127.63")],
        "total": "1,674.63",
        "expect": {"contact.first_name": "Daniel", "contact.last_name": "Okafor", "contact.email": "daniel.okafor@lumendental.example.com",
                   "contact.phone": "+15125550192", "contact.job_title": "Procurement Lead", "company.name": "Lumen Dental Group",
                   "company.address": "77 Pine Avenue, Austin, TX 78702", "invoice.number": "INV-2207", "invoice.issue_date": "2026-10-14",
                   "invoice.due_date": "2026-11-13", "invoice.currency": "USD", "invoice.total": "1674.63"},
    },
    {
        "id": "invoice-02", "layout": "classic", "seller": "Ashby Print Works", "seller_lines": ["4 Mill Lane", "York YO1 7HB", "VAT GB 123 4567 89"],
        "number": "0458", "issue": "03/09/2026", "due": "03/10/2026", "symbol": "£", "code": "GBP",
        "customer": ["Harriet Cole", "Studio Director", "Fennick & Moss Ltd", "12 Calder Street", "Leeds LS1 4AB", "harriet@fennickmoss.example.com", "+44 113 496 0587"],
        "items": [("A2 posters, matte, 250", "1", "640.00", "640.00"), ("Brand booklets, 48pp, 500", "1", "960.00", "960.00"), ("VAT 20%", "", "", "320.00")],
        "total": "1,920.00",
        "expect": {"contact.first_name": "Harriet", "contact.last_name": "Cole", "contact.email": "harriet@fennickmoss.example.com",
                   "contact.phone": "+441134960587", "contact.job_title": "Studio Director", "company.name": "Fennick & Moss Ltd",
                   "company.address": "12 Calder Street, Leeds LS1 4AB", "invoice.number": "0458", "invoice.issue_date": "2026-09-03",
                   "invoice.due_date": "2026-10-03", "invoice.currency": "GBP", "invoice.total": "1920.00"},
    },
    {
        "id": "invoice-03", "layout": "modern", "seller": "Nordlicht Akustik", "seller_lines": ["Torstraße 112", "10119 Berlin", "rechnung@nordlicht.example.com"],
        "number": "RE-2026-0193", "issue": "15.08.2026", "due": "14.09.2026", "symbol": "€", "code": "EUR", "decimal_comma": True,
        "customer": ["Jonas Weber", "Head of Purchasing", "Weber Haustechnik GmbH", "Lindenallee 8", "50968 Köln", "j.weber@weber-haustechnik.example.com", "+49 30 901820"],
        "items": [("Acoustic panels, 120 × 60 cm", "16", "89,00", "1.424,00"), ("Ceiling baffles", "6", "105,00", "630,00"), ("MwSt. 19%", "", "", "390,26")],
        "total": "2.444,26",
        "expect": {"contact.first_name": "Jonas", "contact.last_name": "Weber", "contact.email": "j.weber@weber-haustechnik.example.com",
                   "contact.phone": "+4930901820", "contact.job_title": "Head of Purchasing", "company.name": "Weber Haustechnik GmbH",
                   "company.address": "Lindenallee 8, 50968 Köln", "invoice.number": "RE-2026-0193", "invoice.issue_date": "2026-08-15",
                   "invoice.due_date": "2026-09-14", "invoice.currency": "EUR", "invoice.total": "2444.26"},
    },
    {
        "id": "invoice-04", "layout": "classic", "seller": "Ravi Packaging Co.", "seller_lines": ["Plot 14, Sundar Industrial Estate", "Lahore", "NTN 4410293-7"],
        "number": "INV-PK-311", "issue": "2 October 2026", "due": "1 November 2026", "symbol": "Rs. ", "code": "PKR",
        "customer": ["Ayesha Raza", "Admin Manager", "Indus Textiles", "23-B Gulberg III", "Lahore 54660", "ayesha.raza@industextiles.example.com", "+92 42 35760145"],
        "items": [("Corrugated cartons, 5-ply", "2,000", "52.00", "104,000.00"), ("Printed tape rolls", "300", "95.00", "28,500.00"), ("Sales tax 9.4%", "", "", "12,455.00")],
        "total": "144,955.00",
        "expect": {"contact.first_name": "Ayesha", "contact.last_name": "Raza", "contact.email": "ayesha.raza@industextiles.example.com",
                   "contact.phone": "+924235760145", "contact.job_title": "Admin Manager", "company.name": "Indus Textiles",
                   "company.address": "23-B Gulberg III, Lahore 54660", "invoice.number": "INV-PK-311", "invoice.issue_date": "2026-10-02",
                   "invoice.due_date": "2026-11-01", "invoice.currency": "PKR", "invoice.total": "144955.00"},
    },
    {
        # Billed to a department, so there's no person to name.
        "id": "invoice-05", "layout": "modern", "seller": "Bluegate Fuel Services", "seller_lines": ["2200 Harbor Blvd", "Long Beach, CA 90813", "ar@bluegate.example.com"],
        "number": "BG-88014", "issue": "September 30, 2026", "due": "October 30, 2026", "symbol": "$", "code": "USD",
        "customer": ["Accounts Payable", "", "Grayline Freight Inc.", "5100 Terminal Way", "Seattle, WA 98134", "ap@grayline.example.com", "+1 206 555 0134"],
        "items": [("Diesel, 2,400 gal", "1", "9,120.00", "9,120.00"), ("Delivery fee", "1", "150.00", "150.00")],
        "total": "9,270.00",
        "expect": {"contact.first_name": None, "contact.last_name": None, "contact.email": "ap@grayline.example.com",
                   "contact.phone": "+12065550134", "contact.job_title": None, "company.name": "Grayline Freight Inc.",
                   "company.address": "5100 Terminal Way, Seattle, WA 98134", "invoice.number": "BG-88014", "invoice.issue_date": "2026-09-30",
                   "invoice.due_date": "2026-10-30", "invoice.currency": "USD", "invoice.total": "9270.00"},
    },
    {
        "id": "invoice-06", "layout": "receipt", "seller": "Saltwater Studio", "seller_lines": ["ABN 51 824 753 556", "Shop 3, 18 Bay Street", "Double Bay NSW 2028"],
        "number": "1187", "issue": "21/07/2026", "due": "04/08/2026", "symbol": "", "code": "AUD", "code_in_total": True,
        "customer": ["Priya Nair", "Owner", "Coral Bay Yoga", "40 Ocean Street", "Bondi NSW 2026", "priya@coralbay.example.com", "+61 2 9374 4000"],
        "items": [("Website refresh", "1", "720.00", "720.00"), ("Photo editing, hourly", "2", "35.00", "70.00"), ("GST", "", "", "79.00")],
        "total": "869.00",
        "expect": {"contact.first_name": "Priya", "contact.last_name": "Nair", "contact.email": "priya@coralbay.example.com",
                   "contact.phone": "+61293744000", "contact.job_title": "Owner", "company.name": "Coral Bay Yoga",
                   "company.address": "40 Ocean Street, Bondi NSW 2026", "invoice.number": "1187", "invoice.issue_date": "2026-07-21",
                   "invoice.due_date": "2026-08-04", "invoice.currency": "AUD", "invoice.total": "869.00"},
    },
    {
        "id": "invoice-07", "layout": "classic", "seller": "Hudson Office Interiors", "seller_lines": ["310 W 38th St", "New York, NY 10018", "orders@hudsonoffice.example.com"],
        "number": "HOI-5520", "issue": "Aug 3, 2026", "due": "Sep 2, 2026", "symbol": "$", "code": "USD",
        "customer": ["Marcus Lee", "Facilities Coordinator", "Brightwave Health", "1 Court Square", "Long Island City, NY 11101", "marcus.lee@brightwave.example.com", "+1 718 555 0163"],
        "items": [("Task chairs, mesh", "14", "289.00", "4,046.00"), ("Delivery and assembly", "1", "350.00", "350.00"), ("Loyalty discount", "", "", "-200.00")],
        "total": "4,196.00",
        "expect": {"contact.first_name": "Marcus", "contact.last_name": "Lee", "contact.email": "marcus.lee@brightwave.example.com",
                   "contact.phone": "+17185550163", "contact.job_title": "Facilities Coordinator", "company.name": "Brightwave Health",
                   "company.address": "1 Court Square, Long Island City, NY 11101", "invoice.number": "HOI-5520", "invoice.issue_date": "2026-08-03",
                   "invoice.due_date": "2026-09-02", "invoice.currency": "USD", "invoice.total": "4196.00"},
    },
    {
        # A phone photo of a printed invoice: tilted, a little soft and unevenly lit.
        "id": "invoice-08", "layout": "modern", "photo": True, "seller": "Greenline Cleaning", "seller_lines": ["88 Fulton St", "Brooklyn, NY 11201", "hello@greenline.example.com"],
        "number": "GL-1093", "issue": "11/02/2026", "due": "11/16/2026", "symbol": "$", "code": "USD",
        "customer": ["Sofia Marino", "Office Manager", "Tidewater Legal", "45 Main Street, Suite 600", "Brooklyn, NY 11201", "sofia@tidewaterlegal.example.com", "+1 917 555 0181"],
        "items": [("Weekly office cleaning, October", "4", "260.00", "1,040.00"), ("Carpet deep clean", "1", "310.00", "310.00")],
        "total": "1,350.00",
        "expect": {"contact.first_name": "Sofia", "contact.last_name": "Marino", "contact.email": "sofia@tidewaterlegal.example.com",
                   "contact.phone": "+19175550181", "contact.job_title": "Office Manager", "company.name": "Tidewater Legal",
                   "company.address": "45 Main Street, Suite 600, Brooklyn, NY 11201", "invoice.number": "GL-1093", "invoice.issue_date": "2026-11-02",
                   "invoice.due_date": "2026-11-16", "invoice.currency": "USD", "invoice.total": "1350.00"},
    },
]


def money(invoice, amount):
    if not amount:
        return ""
    if invoice.get("code_in_total"):
        return amount
    if invoice.get("decimal_comma"):
        return f"{amount} {invoice['symbol']}"
    sign, amount = ("-", amount[1:]) if amount.startswith("-") else ("", amount)
    return f"{sign}{invoice['symbol']}{amount}"


def invoice_html(inv):
    name, title, company, *address, email, phone = inv["customer"]
    customer = [f"<strong>{E(name)}</strong>"] + [E(x) for x in (title, company, *address, email, phone) if x]
    rows = "".join(
        f"<tr><td>{E(d)}</td><td class=n>{E(q)}</td><td class=n>{E(money(inv, u))}</td><td class=n>{E(money(inv, a))}</td></tr>"
        for d, q, u, a in inv["items"]
    )
    total = f"{inv['code']} {inv['total']}" if inv.get("code_in_total") else money(inv, inv["total"])
    labels = {
        "modern": ("INVOICE", "Invoice no.", "Issue date", "Due date", "Bill to", "Total due"),
        "classic": ("Tax Invoice", "Invoice #", "Date", "Payment due", "Invoice to", "Amount payable"),
        "receipt": ("TAX INVOICE", "No.", "Date", "Due", "Customer", "TOTAL"),
    }[inv["layout"]]
    heading, l_no, l_issue, l_due, l_to, l_total = labels
    style = {
        "modern": "body{font-family:'Helvetica Neue',Arial,sans-serif;color:#1d2433} header{display:flex;justify-content:space-between;border-bottom:3px solid #2b4c7e;padding-bottom:16px} .brand{font-size:20pt;font-weight:700;color:#2b4c7e} h1{font-weight:300;letter-spacing:.08em;margin:0}",
        "classic": "body{font-family:Georgia,'Times New Roman',serif;color:#222} header{text-align:center;border-bottom:1px double #444;padding-bottom:12px} .brand{font-size:22pt;font-variant:small-caps} h1{font-size:14pt;margin:8px 0 0;text-transform:uppercase;letter-spacing:.2em}",
        "receipt": "body{font-family:'Courier New',monospace;color:#111;font-size:10pt} .page{width:4.5in!important;margin:0 auto} header{text-align:center;border-bottom:1px dashed #111;padding-bottom:10px} .brand{font-size:14pt;font-weight:700} h1{font-size:12pt;margin:8px 0 0}",
    }[inv["layout"]]
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@page {{ size: Letter; margin: 0 }} * {{ box-sizing: border-box }} body {{ margin: 0 }}
.page {{ width: 8.5in; min-height: 11in; padding: .7in .75in; font-size: 10.5pt }}
{style}
.meta {{ display: grid; grid-template-columns: auto auto; gap: 2px 14px; justify-content: end; margin-top: 10px; font-size: 9.5pt }}
.party {{ margin: 24px 0; line-height: 1.5 }} .party h2 {{ font-size: 8pt; letter-spacing: .12em; text-transform: uppercase; margin: 0 0 4px }}
table {{ width: 100%; border-collapse: collapse }} th, td {{ padding: 8px 4px; border-bottom: 1px solid #ccd; text-align: left }} .n {{ text-align: right; white-space: nowrap }}
.total {{ text-align: right; font-size: 13pt; font-weight: 700; margin-top: 14px }} small {{ color: #667 }}
</style></head><body><div class="page">
<header><div><div class="brand">{E(inv['seller'])}</div><small>{'<br>'.join(E(x) for x in inv['seller_lines'])}</small></div>
<div><h1>{heading}</h1><div class="meta"><span>{l_no}</span><b>{E(inv['number'])}</b><span>{l_issue}</span><b>{E(inv['issue'])}</b><span>{l_due}</span><b>{E(inv['due'])}</b></div></div></header>
<div class="party"><h2>{l_to}</h2>{'<br>'.join(customer)}</div>
<table><thead><tr><th>Description</th><th class=n>Qty</th><th class=n>Unit price</th><th class=n>Amount</th></tr></thead><tbody>{rows}</tbody></table>
<div class="total">{l_total}: {E(total)}</div>
<p><small>Thank you for your business. Please quote the invoice number with your payment.</small></p>
</div></body></html>"""


# Business cards, rendered as pictures.

CARDS = [
    {"id": "card-01", "layout": "light", "name": "Mei Tan", "title": "Product Designer", "company": "Harbor & Pine",
     "lines": ["mei.tan@harborpine.example.com", "+65 6123 4567", "harborpine.example.com"], "address": ["8 Marina View, #12-01", "Singapore 018960"],
     "expect": {"contact.first_name": "Mei", "contact.last_name": "Tan", "contact.email": "mei.tan@harborpine.example.com", "contact.phone": "+6561234567",
                "contact.job_title": "Product Designer", "company.name": "Harbor & Pine", "company.domain": "harborpine.example.com",
                "company.address": "8 Marina View, #12-01, Singapore 018960"}},
    {"id": "card-02", "layout": "dark", "name": "Carlos Mendes", "title": "Sales Director", "company": "Atlas Solar",
     "lines": ["carlos@atlassolar.example.com", "+351 912 345 678", "atlassolar.example.com"], "address": ["Av. da Liberdade 190", "1250-147 Lisboa"],
     "expect": {"contact.first_name": "Carlos", "contact.last_name": "Mendes", "contact.email": "carlos@atlassolar.example.com", "contact.phone": "+351912345678",
                "contact.job_title": "Sales Director", "company.name": "Atlas Solar", "company.domain": "atlassolar.example.com",
                "company.address": "Av. da Liberdade 190, 1250-147 Lisboa"}},
    {"id": "card-03", "layout": "light", "name": "Fatima Zahra", "title": "Account Executive", "company": "Brightline Media",
     # No country code, which Inkfold should flag rather than guess.
     "lines": ["fatima.zahra@brightline.example.com", "(020) 7946 0958", "brightline.example.com"], "address": ["22 Old Street", "London EC1V 9AB"],
     "expect": {"contact.first_name": "Fatima", "contact.last_name": "Zahra", "contact.email": "fatima.zahra@brightline.example.com", "contact.phone": "(020) 7946 0958",
                "contact.job_title": "Account Executive", "company.name": "Brightline Media", "company.domain": "brightline.example.com",
                "company.address": "22 Old Street, London EC1V 9AB"}},
    {"id": "card-04", "layout": "dark", "name": "JAMES O'CONNOR", "title": "Head of IT", "company": "Riverbend Credit Union",
     "lines": ["james.oconnor@riverbendcu.example.com", "+1 312 555 0147", "riverbendcu.example.com"], "address": ["200 N LaSalle St", "Chicago, IL 60601"],
     "expect": {"contact.first_name": "James", "contact.last_name": "O'Connor", "contact.email": "james.oconnor@riverbendcu.example.com", "contact.phone": "+13125550147",
                "contact.job_title": "Head of IT", "company.name": "Riverbend Credit Union", "company.domain": "riverbendcu.example.com",
                "company.address": "200 N LaSalle St, Chicago, IL 60601"}},
    {"id": "card-05", "layout": "light", "photo": True, "name": "Hana Kobayashi", "title": "Marketing Manager", "company": "Kumo Tea Co.",
     "lines": ["hana@kumotea.example.com", "+81 3 1234 5678", "kumotea.example.com"], "address": ["2-4-1 Marunouchi", "Chiyoda-ku, Tokyo"],
     "expect": {"contact.first_name": "Hana", "contact.last_name": "Kobayashi", "contact.email": "hana@kumotea.example.com", "contact.phone": "+81312345678",
                "contact.job_title": "Marketing Manager", "company.name": "Kumo Tea Co.", "company.domain": "kumotea.example.com",
                "company.address": "2-4-1 Marunouchi, Chiyoda-ku, Tokyo"}},
    {"id": "card-06", "layout": "dark", "name": "Liam Novak", "title": "Partner", "company": "Novak & Hale Architects",
     # Two numbers and no website.
     "lines": ["liam@novakhale.example.com", "M +420 601 234 567", "O +420 222 123 456"], "address": ["Národní 37", "110 00 Praha 1"],
     "expect": {"contact.first_name": "Liam", "contact.last_name": "Novak", "contact.email": "liam@novakhale.example.com",
                "contact.phone": ["+420601234567", "+420222123456"], "contact.job_title": "Partner", "company.name": "Novak & Hale Architects",
                "company.domain": [None, "novakhale.example.com"], "company.address": "Národní 37, 110 00 Praha 1"}},
]


def card_html(card):
    dark = card["layout"] == "dark"
    photo = card.get("photo")
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body {{ margin: 0; background: {'#8a8478' if photo else '#e6e8ec'} }}
.stage {{ width: 1000px; height: 620px; display: grid; place-items: center; overflow: hidden }}
.card {{ width: 820px; height: 470px; padding: 56px 60px; box-sizing: border-box; border-radius: 12px; position: relative;
  font-family: {'Futura, "Trebuchet MS", sans-serif' if dark else '"Helvetica Neue", Arial, sans-serif'};
  background: {'#18212f' if dark else '#fbfaf6'}; color: {'#f2efe8' if dark else '#1b2230'};
  box-shadow: 0 16px 40px rgba(0,0,0,.25); {'transform: rotate(-4deg); filter: blur(.6px) brightness(.93) contrast(.9);' if photo else ''} }}
.company {{ font-weight: 800; letter-spacing: .18em; text-transform: uppercase; font-size: 18px; color: {'#e3b25b' if dark else '#2f6f5e'} }}
h1 {{ margin: 70px 0 6px; font-size: 42px; font-weight: 600 }} .title {{ font-size: 20px; opacity: .75 }}
.lines {{ position: absolute; left: 60px; bottom: 50px; font-size: 18px; line-height: 1.6 }}
.addr {{ position: absolute; right: 60px; bottom: 50px; text-align: right; font-size: 16px; line-height: 1.5; opacity: .75 }}
.glare {{ position: absolute; inset: 0; background: linear-gradient(115deg, rgba(255,255,255,.35), transparent 45%); border-radius: 12px }}
</style></head><body><div class="stage"><div class="card">
<div class="company">{E(card['company'])}</div><h1>{E(card['name'])}</h1><div class="title">{E(card['title'])}</div>
<div class="lines">{'<br>'.join(E(x) for x in card['lines'])}</div><div class="addr">{'<br>'.join(E(x) for x in card['address'])}</div>
{'<div class="glare"></div>' if photo else ''}</div></div></body></html>"""


# Lead forms, as printed PDFs.

LEADS = [
    {"id": "lead-01", "layout": "typed", "heading": "Clinic partnership enquiry", "org": "MedStock Distribution",
     "rows": [("Full name", "Grace Huang"), ("Job title", "Operations Manager"), ("Organisation", "Northfield Clinics"), ("Email", "grace.huang@northfield.example.com"),
              ("Phone", "+1 646 555 0110"), ("Website", "northfield.example.com"), ("Interested in", "Monthly supply contract for 6 clinics")],
     "expect": {"contact.first_name": "Grace", "contact.last_name": "Huang", "contact.email": "grace.huang@northfield.example.com", "contact.phone": "+16465550110",
                "contact.job_title": "Operations Manager", "company.name": "Northfield Clinics", "company.domain": "northfield.example.com"}},
    {"id": "lead-02", "layout": "handwritten", "heading": "Visitor card", "org": "Casa Verde Expo 2026",
     "rows": [("Name", "Tomás Rivera"), ("Company", "Verde Viva"), ("Role", "Owner"), ("Email", "tomas.rivera@verdeviva.example.com"),
              ("Phone", "+34 612 345 678"), ("I'd like to", "Get a quote for 40 planters")],
     "expect": {"contact.first_name": "Tomás", "contact.last_name": "Rivera", "contact.email": "tomas.rivera@verdeviva.example.com", "contact.phone": "+34612345678",
                "contact.job_title": "Owner", "company.name": "Verde Viva", "company.domain": [None, "verdeviva.example.com"]}},
    {"id": "lead-03", "layout": "typed", "heading": "Demo request", "org": "Ledgerline Software",
     "rows": [("First name", "Noah"), ("Last name", "Fischer"), ("Work email", "noah.fischer@alpenbank.example.com"), ("Phone", "+41 44 668 1800"),
              ("Company", "Alpen Privatbank"), ("Job title", "Head of Finance Operations"), ("Team size", "25–50"), ("Message", "We want to replace spreadsheets for month-end close.")],
     "expect": {"contact.first_name": "Noah", "contact.last_name": "Fischer", "contact.email": "noah.fischer@alpenbank.example.com", "contact.phone": "+41446681800",
                "contact.job_title": "Head of Finance Operations", "company.name": "Alpen Privatbank", "company.domain": [None, "alpenbank.example.com"]}},
    {"id": "lead-04", "layout": "handwritten", "heading": "Wholesale enquiry", "org": "Saffron Route Spices",
     # A local number with no country code: should be flagged, not guessed.
     "rows": [("Name", "Bilal Ahmed"), ("Business", "Crescent Foods"), ("Position", "Purchasing Officer"), ("Email", "bilal@crescentfoods.example.com"),
              ("Mobile", "0300 1234567"), ("City", "Karachi"), ("Needs", "Bulk turmeric and chilli, monthly")],
     "expect": {"contact.first_name": "Bilal", "contact.last_name": "Ahmed", "contact.email": "bilal@crescentfoods.example.com", "contact.phone": "0300 1234567",
                "contact.job_title": "Purchasing Officer", "company.name": "Crescent Foods", "company.domain": [None, "crescentfoods.example.com"]}},
    {"id": "lead-05", "layout": "typed", "heading": "Partner application", "org": "Quanta Cloud",
     "rows": [("Name", "Elena Petrova"), ("Title", "Chief Financial Officer"), ("Company", "Volga Analytics"), ("Company website", "volga-analytics.example.com"),
              ("Email", "e.petrova@volga-analytics.example.com"), ("Phone", "+7 495 123 4567"), ("Address", "Tverskaya St 7, Moscow 125009")],
     "expect": {"contact.first_name": "Elena", "contact.last_name": "Petrova", "contact.email": "e.petrova@volga-analytics.example.com", "contact.phone": "+74951234567",
                "contact.job_title": "Chief Financial Officer", "company.name": "Volga Analytics", "company.domain": "volga-analytics.example.com",
                "company.address": "Tverskaya St 7, Moscow 125009"}},
    {"id": "lead-06", "layout": "webform", "heading": "New contact form submission", "org": "harborline.example.com",
     "rows": [("your-name", "Samuel Okoye"), ("your-email", "sam.okoye@lagosfreight.example.com"), ("company", "Lagos Freight Partners"),
              ("phone", "+234 802 123 4567"), ("subject", "Container tracking"), ("message", "Hi, I run logistics at Lagos Freight Partners and want to see your tracking API.")],
     "expect": {"contact.first_name": "Samuel", "contact.last_name": "Okoye", "contact.email": "sam.okoye@lagosfreight.example.com", "contact.phone": "+2348021234567",
                "contact.job_title": [None, "Head of Logistics", "Logistics"], "company.name": "Lagos Freight Partners", "company.domain": [None, "lagosfreight.example.com"]}},
]


def lead_html(lead):
    hand = lead["layout"] == "handwritten"
    web = lead["layout"] == "webform"
    value_font = '"Z003", "Segoe Print", "Bradley Hand", "Comic Sans MS", "URW Chancery L", cursive' if hand else "inherit"
    rows = "".join(
        f"<div class=row><span class=label>{E(k)}</span><span class=value>{E(v)}</span></div>" for k, v in lead["rows"]
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 0 }} * {{ box-sizing: border-box }} body {{ margin: 0; font-family: {'"Courier New", monospace' if web else '"Helvetica Neue", Arial, sans-serif'}; color: #222 }}
.page {{ width: 210mm; min-height: 297mm; padding: 22mm }}
.org {{ color: #667; font-size: 9pt; border-bottom: 1px solid #ccd; padding-bottom: 8px }}
h1 {{ font-weight: 400; font-size: 22pt; margin: 22px 0 22px }}
.row {{ display: grid; grid-template-columns: {'150px 1fr' if web else '1fr'}; gap: 4px; margin-bottom: 16px }}
.label {{ font-size: {'10pt' if web else '8pt'}; letter-spacing: {'0' if web else '.1em'}; text-transform: {'none' if web else 'uppercase'}; color: #667 }}
.value {{ font-family: {value_font}; font-size: {'17pt' if hand else '12pt'}; color: {'#1a3a8a' if hand else '#111'};
  border-bottom: {'0' if web else '1px solid #99a'}; padding: 2px 4px 4px; {'transform: rotate(-.6deg);' if hand else ''} }}
</style></head><body><div class="page"><div class="org">{E(lead['org'])}</div><h1>{E(lead['heading'])}</h1>{rows}</div></body></html>"""


# Emails, as plain text, the way email intake hands them over.

EMAILS = [
    {"id": "email-01", "text": """Subject: Quote for office plants
From: Nadia Karim <nadia.karim@sablecapital.example.com>

Hi there,

We're moving into a new floor next month and would like a quote for plants and monthly care
for roughly 3,000 sq ft. Could someone call me this week?

Best regards,
Nadia Karim
Workplace Experience Manager
Sable Capital
+44 161 496 0321
sablecapital.example.com
""", "expect": {"contact.first_name": "Nadia", "contact.last_name": "Karim", "contact.email": "nadia.karim@sablecapital.example.com", "contact.phone": "+441614960321",
                 "contact.job_title": "Workplace Experience Manager", "company.name": "Sable Capital", "company.domain": "sablecapital.example.com"}},
    {"id": "email-02", "text": """Subject: Fwd: Partnership
From: Tom Price <tom@ourteam.example.com>

Can you add this one to HubSpot? Looks promising. - Tom

---------- Forwarded message ---------
From: Victor Hale <victor.hale@halerobotics.example.com>
Date: Tue, Oct 27, 2026 at 9:14 AM
Subject: Partnership

Hello,

I lead business development at Hale Robotics. We build picking robots for small warehouses
and would like to explore a reseller partnership in the Midwest.

Victor Hale
VP Business Development, Hale Robotics
+1 415 555 0172
""", "expect": {"contact.first_name": "Victor", "contact.last_name": "Hale", "contact.email": "victor.hale@halerobotics.example.com", "contact.phone": "+14155550172",
                 "contact.job_title": "VP Business Development", "company.name": "Hale Robotics", "company.domain": [None, "halerobotics.example.com"]}},
    {"id": "email-03", "text": """Subject: Re: invoice question
From: ben@pixelforge.example.com

Thanks, that works for us.

--
Ben Carter | CTO | Pixelforge Games
ben@pixelforge.example.com | +1 312 555 0147
www.pixelforge.example.com
""", "expect": {"contact.first_name": "Ben", "contact.last_name": "Carter", "contact.email": "ben@pixelforge.example.com", "contact.phone": "+13125550147",
                 "contact.job_title": "CTO", "company.name": "Pixelforge Games", "company.domain": "pixelforge.example.com"}},
    {"id": "email-04", "text": """Notes from the expo, day 2

Met Zara Ali from Lumina Events. She plans 30+ corporate events a year and wants a quote for
our AV package. Prefers email: zara@luminaevents.example.com. No phone given.
""", "expect": {"contact.first_name": "Zara", "contact.last_name": "Ali", "contact.email": "zara@luminaevents.example.com", "contact.phone": None,
                 "contact.job_title": None, "company.name": "Lumina Events", "company.domain": [None, "luminaevents.example.com"]}},
]


def render_pdf(source, target):
    subprocess.run([CHROME, "--headless=new", "--no-pdf-header-footer", f"--print-to-pdf={target}", source.as_uri()],
                   check=True, capture_output=True)


def render_png(source, target, width=1000, height=620, scale=1.5):
    subprocess.run([CHROME, "--headless=new", "--hide-scrollbars", f"--force-device-scale-factor={scale}",
                    f"--window-size={width},{height}", f"--screenshot={target}", source.as_uri()], check=True, capture_output=True)


def expected(case_id, filename, doc_type, values):
    fields = {key: None for key in FIELD_KEYS}
    fields.update(values)
    return {"id": case_id, "file": filename, "doc_type": doc_type, "fields": fields}


def main():
    CASES.mkdir(exist_ok=True)
    answers = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for inv in INVOICES:
            source = tmp / f"{inv['id']}.html"
            source.write_text(invoice_html(inv), encoding="utf-8")
            if inv.get("photo"):
                # The top of the page, photographed at a slight angle.
                photo = tmp / f"{inv['id']}-photo.html"
                photo.write_text(f"""<!doctype html><html><body style="margin:0;background:#6d665c;overflow:hidden">
<iframe src="{source.as_uri()}" style="width:816px;height:900px;border:0;background:#fff;transform:translate(40px,30px) rotate(2.5deg);
filter:blur(.5px) brightness(.92) contrast(.88);box-shadow:0 10px 30px rgba(0,0,0,.4)"></iframe></body></html>""", encoding="utf-8")
                render_png(photo, CASES / f"{inv['id']}.png", width=900, height=960, scale=1.25)
                filename = f"{inv['id']}.png"
            else:
                render_pdf(source, CASES / f"{inv['id']}.pdf")
                filename = f"{inv['id']}.pdf"
            answers.append(expected(inv["id"], filename, "invoice", inv["expect"]) | {"line_items": len(inv["items"])})
        for card in CARDS:
            source = tmp / f"{card['id']}.html"
            source.write_text(card_html(card), encoding="utf-8")
            render_png(source, CASES / f"{card['id']}.png")
            answers.append(expected(card["id"], f"{card['id']}.png", "business_card", card["expect"]))
        for lead in LEADS:
            source = tmp / f"{lead['id']}.html"
            source.write_text(lead_html(lead), encoding="utf-8")
            render_pdf(source, CASES / f"{lead['id']}.pdf")
            answers.append(expected(lead["id"], f"{lead['id']}.pdf", "lead_form", lead["expect"]))
    for mail in EMAILS:
        (CASES / f"{mail['id']}.txt").write_text(mail["text"], encoding="utf-8")
        answers.append(expected(mail["id"], f"{mail['id']}.txt", ["lead_form", "other"], mail["expect"]))
    (HERE / "expected.json").write_text(json.dumps(answers, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(answers)} cases to {CASES}")


if __name__ == "__main__":
    main()
