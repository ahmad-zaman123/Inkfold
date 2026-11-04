import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api.js'
import FieldRow from '../components/FieldRow.jsx'
import LineItems from '../components/LineItems.jsx'
import SyncPanel from '../components/SyncPanel.jsx'
import { DOC_TYPES, GROUPS } from '../labels.js'

const POLL_MS = 2000
const POLL_LIMIT_MS = 2 * 60 * 1000

function valuesOf(document) {
  return Object.fromEntries(Object.entries(document.fields).map(([key, field]) => [key, field.value]))
}

const PENDING = ['needs_review', 'failed']

// The next document waiting for review, in the order they arrived, starting after this one.
function nextToReview(documents, current) {
  const waiting = documents
    .filter((d) => d.id !== current.id && PENDING.includes(d.status))
    .sort((a, b) => a.created_at.localeCompare(b.created_at))
  return { next: waiting.find((d) => d.created_at > current.created_at) || waiting[0], left: waiting.length }
}

function changedKeys(document, values) {
  return Object.keys(values).filter((key) => JSON.stringify(values[key]) !== JSON.stringify(document.fields[key].value))
}

// Keyed by id, so moving to the next document starts with a clean page.
export default function ReviewPage() {
  const { id } = useParams()
  return <Review key={id} id={id} />
}

function Review({ id }) {
  const navigate = useNavigate()
  const location = useLocation()
  const [document, setDocument] = useState(null)
  const [values, setValues] = useState({})
  const [loadError, setLoadError] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState('')
  const [notice, setNotice] = useState(location.state?.duplicate ? 'You uploaded this file before, so here is the earlier copy.' : '')
  const pollStarted = useRef(null)
  const [others, setOthers] = useState([])
  const [matches, setMatches] = useState(null)

  const show = useCallback((next) => {
    setDocument(next)
    setValues(valuesOf(next))
  }, [])

  useEffect(() => {
    api.document(id).then(show).catch((err) => setLoadError(err.status === 404 ? 'This document no longer exists.' : err.message))
  }, [id, show])

  useEffect(() => {
    api.documents().then((data) => setOthers(data.documents)).catch(() => {})
  }, [id])

  // While n8n works, check back every two seconds, for up to two minutes.
  useEffect(() => {
    if (document?.status !== 'syncing') {
      pollStarted.current = null
      return undefined
    }
    pollStarted.current ??= Date.now()
    if (Date.now() - pollStarted.current > POLL_LIMIT_MS) return undefined
    const timer = setTimeout(() => {
      api.document(id).then((next) => {
        if (next.status !== 'syncing') show(next)
        else setDocument(next)
      }).catch(() => {})
    }, POLL_MS)
    return () => clearTimeout(timer)
  }, [document, id, show])

  // Look up what HubSpot already has once the saved fields are ready to send, and again after each save.
  const lookupKey = document?.can_sync ? document.updated_at : null
  useEffect(() => {
    setMatches(null)
    if (!lookupKey) return undefined
    let current = true
    api.matches(id).then((found) => current && setMatches(found)).catch(() => {})
    return () => {
      current = false
    }
  }, [id, lookupKey])

  const dirtyKeys = useMemo(() => (document ? changedKeys(document, values) : []), [document, values])
  const dirty = dirtyKeys.length > 0

  useEffect(() => {
    if (!dirty) return undefined
    const warn = (event) => event.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  if (loadError) {
    return (
      <div className="page">
        <p className="banner error" role="alert">{loadError}</p>
        <Link to="/">Back to documents</Link>
      </div>
    )
  }
  if (!document) return <div className="page"><p className="muted" aria-busy="true">Loading...</p></div>

  const queue = nextToReview(others, document)
  const locked = document.status === 'syncing' && !document.can_sync
  const hasInvoice = document.doc_type === 'invoice' || GROUPS[2].keys.some((key) => document.fields[key].value !== null)
  const groups = GROUPS.filter((group) => group.title !== 'Invoice' || hasInvoice)
  const toCheck = Object.values(document.fields).filter((f) => f.value !== null && !f.edited && (f.issues.length || f.confidence < 0.7)).length

  async function save() {
    setSaving(true)
    setSaveError('')
    setNotice('')
    try {
      show(await api.saveFields(id, Object.fromEntries(dirtyKeys.map((key) => [key, values[key]]))))
    } catch (err) {
      setSaveError(err.message)
    } finally {
      setSaving(false)
    }
  }

  async function send() {
    setSending(true)
    setSendError('')
    try {
      show(await api.sync(id))
    } catch (err) {
      setSendError(err.message)
    } finally {
      setSending(false)
    }
  }

  async function remove() {
    if (!window.confirm(`Delete ${document.filename}? This doesn't touch anything already in HubSpot.`)) return
    try {
      await api.remove(id)
      navigate('/')
    } catch (err) {
      setSaveError(err.message)
    }
  }

  return (
    <div className="page review">
      <div className="review-nav">
        <Link to="/" className="back">← Documents</Link>
        {queue.next && (
          <Link to={`/documents/${queue.next.id}`} className="back">
            Next to review{queue.left > 1 ? ` (${queue.left} waiting)` : ''} →
          </Link>
        )}
      </div>
      <div className="review-head">
        <div>
          <h1>{document.title}</h1>
          <p className="doc-meta">{DOC_TYPES[document.doc_type]} · {document.filename}</p>
          {document.source?.via === 'email' && (
            <p className="doc-meta">
              Emailed in by {document.source.from || 'an unknown sender'}
              {document.source.subject && <>: &ldquo;{document.source.subject}&rdquo;</>}
            </p>
          )}
        </div>
        <button type="button" className="button subtle" onClick={remove}>Delete</button>
      </div>
      {notice && <p className="banner info" role="status">{notice}</p>}
      {document.status === 'failed' && (
        <p className="banner error">{document.error} You can still type the details in below.</p>
      )}
      {toCheck > 0 && document.status === 'needs_review' && (
        <p className="banner warn" role="status">
          {toCheck === 1 ? '1 field needs' : `${toCheck} fields need`} a closer look before you send this.
        </p>
      )}

      <div className="review-grid">
        <form className="fields" onSubmit={(e) => { e.preventDefault(); save() }}>
          {groups.map((group) => (
            <fieldset key={group.title} className="card group">
              <legend>{group.title}</legend>
              {group.keys.map((key) =>
                key === 'invoice.line_items' ? (
                  <LineItems key={key} field={document.fields[key]} items={values[key]} disabled={locked}
                    onChange={(items) => setValues({ ...values, [key]: items.length ? items : null })} />
                ) : (
                  <FieldRow key={key} name={key} field={document.fields[key]} value={values[key]} disabled={locked}
                    onChange={(value) => setValues({ ...values, [key]: value })} />
                ),
              )}
            </fieldset>
          ))}
          <div className={`savebar${dirty ? ' visible' : ''}`} aria-hidden={!dirty}>
            <span>{dirtyKeys.length === 1 ? '1 unsaved change' : `${dirtyKeys.length} unsaved changes`}</span>
            {saveError && <span className="savebar-error" role="alert">{saveError}</span>}
            <button type="button" className="button subtle" onClick={() => setValues(valuesOf(document))} disabled={saving || !dirty} tabIndex={dirty ? 0 : -1}>
              Discard
            </button>
            <button type="submit" className="button primary" disabled={saving || !dirty} tabIndex={dirty ? 0 : -1}>
              {saving ? 'Saving...' : 'Save changes'}
            </button>
          </div>
        </form>
        <SyncPanel document={document} matches={dirty ? null : matches} dirty={dirty} sending={sending} error={sendError} onSend={send} />
      </div>
    </div>
  )
}
