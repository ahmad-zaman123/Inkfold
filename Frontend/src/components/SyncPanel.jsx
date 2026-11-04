import { formatDay, formatWhen } from '../labels.js'
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

function Existing({ match, children }) {
  return (
    <li>
      {children}
      {match.updated_at && <>, updated {formatDay(match.updated_at)}</>}.
      {match.url && <> <a href={match.url} target="_blank" rel="noreferrer">Open</a></>}
    </li>
  )
}

// What sending would touch that's already in HubSpot. Records this document made itself are left out.
function AlreadyThere({ matches }) {
  const fresh = (kind) => (matches?.available && matches[kind] && !matches[kind].own ? matches[kind] : null)
  const contact = fresh('contact')
  const company = fresh('company')
  const deal = fresh('deal')
  return (
    <>
      {deal && (
        <div className="banner warn existing" role="status">
          <p className="existing-title">This deal may already be in HubSpot</p>
          <ul>
            <Existing match={deal}>
              &ldquo;{deal.name}&rdquo;{Number.isFinite(Number(deal.amount)) && deal.amount !== null && <> ({Number(deal.amount).toLocaleString()})</>}
            </Existing>
          </ul>
          <p>Sending adds a second deal with the same name.</p>
        </div>
      )}
      {(contact || company) && (
        <div className="banner info existing" role="status">
          <p className="existing-title">Already in HubSpot</p>
          <ul>
            {contact && <Existing match={contact}>Contact {contact.name}</Existing>}
            {company && <Existing match={company}>Company {company.name}</Existing>}
          </ul>
          <p>Sending updates {contact && company ? 'them' : 'it'} instead of adding {contact && company ? 'new ones' : 'a new one'}.</p>
        </div>
      )}
    </>
  )
}

export default function SyncPanel({ document, matches, dirty, sending, error, onSend }) {
  const reason = blockedReason(document, dirty)
  const duplicateDeal = matches?.available && matches.deal && !matches.deal.own
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

      {!reason && <AlreadyThere matches={matches} />}
      <button type="button" className="button primary wide" onClick={onSend} disabled={Boolean(reason) || sending || !document.can_sync}>
        {sending || document.status === 'syncing' ? 'Sending...' : document.status === 'sync_failed' ? 'Try again' : duplicateDeal && !reason ? 'Send anyway' : 'Send to HubSpot'}
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
