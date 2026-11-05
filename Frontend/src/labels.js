export const DOC_TYPES = {
  invoice: 'Invoice',
  receipt: 'Receipt',
  purchase_order: 'Purchase order',
  business_card: 'Business card',
  lead_form: 'Lead form',
  resume: 'Resume',
  other: 'Other document',
}

const MONEY_TYPES = ['invoice', 'receipt', 'purchase_order']

export const STATUSES = {
  needs_review: { label: 'Needs review', tone: 'info' },
  failed: { label: "Couldn't read", tone: 'error' },
  syncing: { label: 'Sending', tone: 'busy' },
  synced: { label: 'Sent', tone: 'ok' },
  sync_failed: { label: 'Send failed', tone: 'error' },
}

const MONEY_TITLES = { invoice: 'Invoice', receipt: 'Receipt', purchase_order: 'Purchase order' }
const CANDIDATE_KEYS = ['contact.location', 'contact.linkedin', 'resume.skills']

// The groups of fields the review page shows for a kind of document. A field outside its usual
// document type still shows when it has a value, so nothing read is ever hidden.
export function groupsFor(docType, fields) {
  const has = (key) => fields[key]?.value !== null && fields[key]?.value !== undefined
  const keep = (keys, usual) => keys.filter((key) => usual || has(key))
  const money = MONEY_TYPES.includes(docType)
  const groups = [
    { title: docType === 'resume' ? 'Candidate' : 'Contact', keys: ['contact.first_name', 'contact.last_name', 'contact.email', 'contact.phone', 'contact.job_title'] },
    { title: docType === 'receipt' ? 'Paid to' : docType === 'resume' ? 'Current employer' : 'Company', keys: ['company.name', 'company.domain', 'company.address'] },
    {
      title: MONEY_TITLES[docType] || 'Invoice',
      keys: [
        ...keep(['invoice.number', 'invoice.issue_date'], money),
        ...keep(['invoice.due_date'], money && docType !== 'receipt'),
        ...keep(['invoice.currency', 'invoice.total'], money),
        ...keep(['invoice.payment_method'], docType === 'receipt'),
        ...keep(['invoice.line_items'], money),
      ],
    },
    { title: docType === 'resume' ? 'Resume' : 'More details', keys: keep(CANDIDATE_KEYS, docType === 'resume') },
    { title: 'Notes', keys: ['notes'] },
  ]
  return groups.filter((group) => group.keys.length)
}

const TYPE_LABELS = {
  receipt: { 'invoice.number': 'Receipt number', 'invoice.issue_date': 'Date paid' },
  purchase_order: { 'invoice.number': 'PO number', 'invoice.issue_date': 'Order date', 'invoice.due_date': 'Delivery date' },
  resume: { 'contact.job_title': 'Current role' },
}

export function labelFor(key, docType) {
  return TYPE_LABELS[docType]?.[key] || FIELD_LABELS[key]
}

export const FIELD_LABELS = {
  'contact.first_name': 'First name',
  'contact.last_name': 'Last name',
  'contact.email': 'Email',
  'contact.phone': 'Phone',
  'contact.job_title': 'Job title',
  'contact.location': 'Location',
  'contact.linkedin': 'LinkedIn',
  'company.name': 'Company',
  'company.domain': 'Website',
  'company.address': 'Address',
  'invoice.number': 'Invoice number',
  'invoice.issue_date': 'Issue date',
  'invoice.due_date': 'Due date',
  'invoice.currency': 'Currency',
  'invoice.total': 'Total',
  'invoice.payment_method': 'Paid with',
  'invoice.line_items': 'Line items',
  'resume.skills': 'Skills',
  notes: 'Notes',
}

export const PLACEHOLDERS = {
  'contact.phone': '+1 415 555 0100',
  'company.domain': 'example.com',
  'invoice.issue_date': 'YYYY-MM-DD',
  'invoice.due_date': 'YYYY-MM-DD',
  'invoice.currency': 'USD',
  'invoice.total': '0.00',
  'contact.linkedin': 'linkedin.com/in/name',
  'resume.skills': 'Python, SQL, project management',
  'invoice.payment_method': 'Visa ending 4242',
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
