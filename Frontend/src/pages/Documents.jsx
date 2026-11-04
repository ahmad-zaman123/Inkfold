import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'
import StatusPill from '../components/StatusPill.jsx'
import Upload from '../components/Upload.jsx'
import { DOC_TYPES, formatWhen } from '../labels.js'

const SAMPLES = [
  ['/samples/invoice.pdf', 'Invoice'],
  ['/samples/business-card.png', 'Business card'],
  ['/samples/lead-form.pdf', 'Lead form'],
]

export default function Documents({ user }) {
  const [documents, setDocuments] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api
      .documents()
      .then((data) => setDocuments(data.documents))
      .catch((err) => setError(err.message))
  }, [])

  return (
    <div className="page">
      <div className="page-head">
        <h1>Documents</h1>
        {user.is_demo && <p className="lede small">Your demo already has the three samples, read and ready to review.</p>}
      </div>

      <Upload />
      <p className="samples">
        No document to hand? Download a sample:{' '}
        {SAMPLES.map(([href, label], index) => (
          <span key={href}>
            {index > 0 && ' · '}
            <a href={href} download>{label}</a>
          </span>
        ))}
      </p>

      {error && <p className="banner error" role="alert">{error}</p>}
      {documents === null && !error && <p className="muted" aria-busy="true">Loading documents...</p>}
      {documents?.length === 0 && <p className="empty">Nothing here yet. Upload a document to get started.</p>}
      {documents?.length > 0 && (
        <ul className="doc-list">
          {documents.map((document) => (
            <li key={document.id}>
              <Link to={`/documents/${document.id}`} className="doc-row">
                <span className="doc-title">{document.title}</span>
                <span className="doc-meta">
                  {DOC_TYPES[document.doc_type]} · {document.filename} · {formatWhen(document.created_at)}
                </span>
                <StatusPill status={document.status} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
