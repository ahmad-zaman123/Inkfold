import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'

const STEPS = [
  ['Upload', 'A PDF, photo or text file: an invoice, receipt, purchase order, business card, lead form or resume.'],
  ['Check', 'Each field shows how sure the AI is and the exact text it came from. Anything doubtful is flagged.'],
  ['Send', 'The contact, company, deal and a note go to HubSpot or Pipedrive, or a row goes to a Google Sheet. Your team picks where.'],
]

export default function Landing({ onDemo }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function startDemo() {
    setBusy(true)
    setError('')
    try {
      onDemo(await api.demo())
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  return (
    <div className="landing">
      <section className="hero">
        <div className="hero-copy">
          <h1>Documents in, CRM records out</h1>
          <p className="lede">
            Gemini reads invoices, receipts, business cards and resumes and fills in the fields. You check anything it wasn't sure about, then send it to
            HubSpot, Pipedrive or Google Sheets in one click.
          </p>
          <div className="actions">
            <button type="button" className="button primary" onClick={startDemo} disabled={busy}>
              {busy ? 'Opening the demo...' : 'Try the live demo'}
            </button>
            <Link to="/register" className="button">
              Create an account
            </Link>
          </div>
          {error && <p className="banner error" role="alert">{error}</p>}
          <p className="fineprint">The demo opens a private account with three sample documents. No sign-up needed.</p>
        </div>

        <figure className="example" aria-label="Example: a business card becomes a HubSpot contact">
          <div className="example-doc">
            <span className="example-tag">business-card.png</span>
            <strong>Omar Siddiqui</strong>
            <span>Head of Operations</span>
            <span className="mono">omar.siddiqui@kestrel-logistics.example.com</span>
            <span className="mono">+44 20 7946 0731</span>
          </div>
          <div className="example-fields">
            <div className="example-row"><span>Email</span><b className="mono">omar.siddiqui@kestrel-logistics.example.com</b><i>96%</i></div>
            <div className="example-row"><span>Phone</span><b className="mono">+442079460731</b><i>95%</i></div>
            <div className="example-row warn"><span>Address</span><b>18 Wharf Road, London</b><i>Check this</i></div>
          </div>
          <div className="example-crm">
            <span className="dot" aria-hidden="true" />
            Saved in HubSpot: contact, company and note
          </div>
        </figure>
      </section>

      <section className="steps" aria-label="How it works">
        {STEPS.map(([title, text], index) => (
          <div key={title} className="step">
            <span className="step-number">{index + 1}</span>
            <h2>{title}</h2>
            <p>{text}</p>
          </div>
        ))}
      </section>

      <footer className="footer">
        Built with FastAPI, Gemini, n8n and React by Ahmad Zaman.{' '}
        <a href="https://github.com/ahmad-zaman123/Inkfold" target="_blank" rel="noreferrer">
          Source on GitHub
        </a>
      </footer>
    </div>
  )
}
