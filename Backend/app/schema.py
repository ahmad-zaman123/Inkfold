DOC_TYPES = ("invoice", "business_card", "lead_form", "other")

# Ordered as the review screen shows them. The type decides how a value is checked and cleaned.
FIELDS = {
    "contact.first_name": {"label": "First name", "type": "text"},
    "contact.last_name": {"label": "Last name", "type": "text"},
    "contact.email": {"label": "Email", "type": "email"},
    "contact.phone": {"label": "Phone", "type": "phone"},
    "contact.job_title": {"label": "Job title", "type": "text"},
    "company.name": {"label": "Company", "type": "text"},
    "company.domain": {"label": "Website", "type": "domain"},
    "company.address": {"label": "Address", "type": "text"},
    "invoice.number": {"label": "Invoice number", "type": "text"},
    "invoice.issue_date": {"label": "Issue date", "type": "date"},
    "invoice.due_date": {"label": "Due date", "type": "date"},
    "invoice.currency": {"label": "Currency", "type": "currency"},
    "invoice.total": {"label": "Total", "type": "money"},
    "invoice.line_items": {"label": "Line items", "type": "line_items"},
    "notes": {"label": "Notes", "type": "text"},
}

# HubSpot matches contacts by email, so nothing can be sent without one.
REQUIRED_FOR_SYNC = ("contact.email",)

EDITED_SOURCE = "Edited in Inkfold"
