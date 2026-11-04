import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { formatWhen } from '../labels.js'

const STATUS_CLASS = { added: 'ok', duplicate: 'muted', skipped: 'warn', failed: 'error' }

/** The account's intake address and what recent emails turned into. Hidden when intake is off. */
export default function EmailIntake({ onNewDocuments }) {
  const [intake, setIntake] = useState(null)
  const [busy, setBusy] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    api
      .intake()
      .then(setIntake)
      .catch(() => setIntake({ enabled: false }))
  }, [])

  if (!intake?.enabled) return null

  async function check() {
    setBusy('check')
    setError('')
    setMessage('')
    try {
      const result = await api.checkIntake()
      setIntake(result)
      setMessage(result.handled ? `Read ${result.handled} new email${result.handled === 1 ? '' : 's'}.` : 'No new emails.')
      if (result.handled) onNewDocuments()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy('')
    }
  }

  async function replace() {
    if (!window.confirm('Make a new address? Emails sent to the old one will no longer arrive.')) return
    setBusy('address')
    setError('')
    setMessage('')
    try {
      setIntake(await api.newIntakeAddress())
      setMessage('Your new address is ready.')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy('')
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(intake.address)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      setError('Copy failed. Select the address and copy it by hand.')
    }
  }

  return (
    <section className="card intake" aria-labelledby="intake-heading">
      <div className="intake-head">
        <div>
          <h2 id="intake-heading">Or email them in</h2>
          <p className="intake-lead">
            Forward invoices, business cards or lead emails to your private address. Attachments are read, and an email
            with no attachment is read from its text.{' '}
            {intake.scheduled
              ? 'New mail is picked up about every 10 minutes, or straight away with Check inbox now.'
              : 'After sending, press Check inbox now.'}
          </p>
        </div>
      </div>
      <div className="intake-address">
        <code className="mono">{intake.address}</code>
        <button type="button" className="button" onClick={copy}>{copied ? 'Copied' : 'Copy'}</button>
        <button type="button" className="button primary" onClick={check} disabled={Boolean(busy)}>
          {busy === 'check' ? 'Checking...' : 'Check inbox now'}
        </button>
      </div>
      <p className="hint intake-status" role="status">
        {message && <span>{message} </span>}
        <button type="button" className="link-button" onClick={replace} disabled={Boolean(busy)}>
          Get a new address
        </button>
      </p>
      {error && <p className="banner error" role="alert">{error}</p>}
      {intake.recent.length > 0 && (
        <ul className="intake-recent" aria-label="Recent emails">
          {intake.recent.map((email) => (
            <li key={`${email.created_at}-${email.subject}`}>
              <span className="intake-subject">{email.subject || '(no subject)'}</span>
              <span className={`intake-result ${STATUS_CLASS[email.status] || ''}`}>{email.detail}</span>
              <span className="intake-when">{email.sender} · {formatWhen(email.created_at)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
