export const DOC_TYPES = {
  invoice: 'Invoice',
  business_card: 'Business card',
  lead_form: 'Lead form',
  other: 'Other document',
}

export const STATUSES = {
  needs_review: { label: 'Needs review', tone: 'info' },
  failed: { label: "Couldn't read", tone: 'error' },
  syncing: { label: 'Sending', tone: 'busy' },
  synced: { label: 'In HubSpot', tone: 'ok' },
  sync_failed: { label: 'Send failed', tone: 'error' },
}

export const GROUPS = [
  { title: 'Contact', keys: ['contact.first_name', 'contact.last_name', 'contact.email', 'contact.phone', 'contact.job_title'] },
  { title: 'Company', keys: ['company.name', 'company.domain', 'company.address'] },
  {
    title: 'Invoice',
    keys: ['invoice.number', 'invoice.issue_date', 'invoice.due_date', 'invoice.currency', 'invoice.total', 'invoice.line_items'],
  },
  { title: 'Notes', keys: ['notes'] },
]

export const FIELD_LABELS = {
  'contact.first_name': 'First name',
  'contact.last_name': 'Last name',
  'contact.email': 'Email',
  'contact.phone': 'Phone',
  'contact.job_title': 'Job title',
  'company.name': 'Company',
  'company.domain': 'Website',
  'company.address': 'Address',
  'invoice.number': 'Invoice number',
  'invoice.issue_date': 'Issue date',
  'invoice.due_date': 'Due date',
  'invoice.currency': 'Currency',
  'invoice.total': 'Total',
  'invoice.line_items': 'Line items',
  notes: 'Notes',
}

export const PLACEHOLDERS = {
  'contact.phone': '+1 415 555 0100',
  'company.domain': 'example.com',
  'invoice.issue_date': 'YYYY-MM-DD',
  'invoice.due_date': 'YYYY-MM-DD',
  'invoice.currency': 'USD',
  'invoice.total': '0.00',
}

// Below this the review screen asks the user to check the value.
export const LOW_CONFIDENCE = 0.7

export const ACCEPTED = '.pdf,.png,.jpg,.jpeg,.txt'
export const MAX_BYTES = 4 * 1024 * 1024

export function formatDay(iso) {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

export function formatWhen(iso) {
  return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

// "you" for the signed-in person, otherwise their email, for saying who added, edited or sent something.
export function who(email, me) {
  return email && me && email === me.email ? 'you' : email
}

// Only worth saying who did what once more than one person shares the documents.
export function inTeam(user) {
  return (user?.team?.size ?? 1) > 1
}

export function teamName(name) {
  return name || 'your team'
}
