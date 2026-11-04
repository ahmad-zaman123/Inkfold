import { formatWhen } from '../labels.js'
import StatusPill from './StatusPill.jsx'

const CHANNELS = { direct: 'HubSpot', n8n: 'n8n workflow' }
const EVENT_LABELS = { sent: 'Sent', synced: 'Saved', failed: 'Failed' }

function blockedReason(document, dirty) {
  if (dirty) return 'Save your changes first.'
  if (document.status === 'syncing' && !document.can_sync) return 'Waiting for HubSpot to confirm.'
  if (document.status === 'synced') return 'Change a field to send it again.'
  if (document.status === 'failed') return 'Fill in the fields and save them first.'
  if (document.missing.length) return 'Add a valid email first. HubSpot matches contacts by email.'
  return ''
}

export default function SyncPanel({ document, dirty, sending, error, onSend }) {
  const reason = blockedReason(document, dirty)
  const crm = document.crm
  const links = crm && [
    crm.contact_url && ['Open contact', crm.contact_url],
    crm.deal_url && ['Open deal', crm.deal_url],
  ].filter(Boolean)

  return (
    <aside className="card sync" aria-labelledby="sync-heading">
      <div className="sync-head">
        <h2 id="sync-heading">HubSpot</h2>
        <StatusPill status={document.status} />
      </div>

      <button type="button" className="button primary wide" onClick={onSend} disabled={Boolean(reason) || sending || !document.can_sync}>
        {sending || document.status === 'syncing' ? 'Sending...' : document.status === 'sync_failed' ? 'Try again' : 'Send to HubSpot'}
      </button>
      <p className="hint" aria-live="polite">{reason}</p>
      {error && <p className="banner error" role="alert">{error}</p>}
      {document.status === 'sync_failed' && document.error && !error && <p className="banner error">{document.error}</p>}

      {crm && (
        <div className="crm">
          <p className="crm-title">Records in HubSpot</p>
          <dl>
            {[['Contact', crm.contact_id], ['Company', crm.company_id], ['Deal', crm.deal_id], ['Note', crm.note_id]]
              .filter(([, id]) => id)
              .map(([label, id]) => (
                <div key={label}><dt>{label}</dt><dd className="mono">{id}</dd></div>
              ))}
          </dl>
          {links?.length > 0 && (
            <p className="crm-links">
              {links.map(([label, href]) => (
                <a key={href} href={href} target="_blank" rel="noreferrer">{label}</a>
              ))}
            </p>
          )}
        </div>
      )}

      {document.sync_events.length > 0 && (
        <div className="timeline">
          <p className="crm-title">History</p>
          <ol>
            {[...document.sync_events].reverse().map((event) => (
              <li key={event.id} className={`event event-${event.status}`}>
                <span className="event-when">{formatWhen(event.created_at)}</span>
                <span className="event-what">{EVENT_LABELS[event.status] || event.status} · {CHANNELS[event.channel] || event.channel}</span>
                {event.detail && <span className="event-detail">{event.detail}</span>}
              </li>
            ))}
          </ol>
        </div>
      )}
    </aside>
  )
}
