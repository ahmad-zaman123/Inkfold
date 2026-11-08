import { formatDay, formatWhen, inTeam, who } from '../labels.js'
import StatusPill from './StatusPill.jsx'

const CHANNELS = { direct: 'HubSpot', n8n: 'n8n workflow', pipedrive: 'Pipedrive', sheets: 'Google Sheets', practice: 'Practice send' }
const EVENT_LABELS = { sent: 'Sent', synced: 'Saved', failed: 'Failed' }
const DESTINATION_LABELS = { hubspot: 'HubSpot', pipedrive: 'Pipedrive', sheets: 'Google Sheets' }
// What each CRM calls the four records.
const RECORD_NAMES = {
  hubspot: ['Contact', 'Company', 'Deal', 'Note'],
  pipedrive: ['Person', 'Organization', 'Deal', 'Note'],
}

function blockedReason(document, dirty, label) {
  if (!document.destination) return 'Nothing to send to yet. Connect HubSpot, Pipedrive or Google Sheets on the server.'
  if (dirty) return 'Save your changes first.'
  if (document.status === 'syncing' && !document.can_sync) return `Waiting for ${label} to confirm.`
  if (document.status === 'synced' && !document.can_sync) return 'Change a field to send it again.'
  if (document.status === 'failed') return 'Fill in the fields and save them first.'
  if (document.missing.length) return `Add a valid email first. ${label} matches people by email.`
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

// What sending would touch that's already in the CRM. Records this document made itself are left out.
function AlreadyThere({ matches, label, names }) {
  const fresh = (kind) => (matches?.available && matches[kind] && !matches[kind].own ? matches[kind] : null)
  const contact = fresh('contact')
  const company = fresh('company')
  const deal = fresh('deal')
  return (
    <>
      {deal && (
        <div className="banner warn existing" role="status">
          <p className="existing-title">This deal may already be in {label}</p>
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
          <p className="existing-title">Already in {label}</p>
          <ul>
            {contact && <Existing match={contact}>{names[0]} {contact.name}</Existing>}
            {company && <Existing match={company}>{names[1]} {company.name}</Existing>}
          </ul>
          <p>Sending updates {contact && company ? 'them' : 'it'} instead of adding {contact && company ? 'new ones' : 'a new one'}.</p>
        </div>
      )}
    </>
  )
}

export default function SyncPanel({ document, user, matches, dirty, sending, error, onSend }) {
  const destination = document.destination?.key
  const label = document.destination?.label || 'HubSpot'
  const reason = blockedReason(document, dirty, label)
  const duplicateDeal = matches?.available && matches.deal && !matches.deal.own
  const crm = document.crm
  // Records saved somewhere other than where the team sends now are still shown, under their own name.
  const savedIn = crm?.destination || 'hubspot'
  const names = RECORD_NAMES[savedIn] || RECORD_NAMES.hubspot
  const links = crm && [
    crm.contact_url && [`Open ${names[0].toLowerCase()}`, crm.contact_url],
    crm.deal_url && ['Open deal', crm.deal_url],
    crm.sheet_url && ['Open sheet', crm.sheet_url],
  ].filter(Boolean)

  return (
    <aside className="card sync" aria-labelledby="sync-heading">
      <div className="sync-head">
        <h2 id="sync-heading">{label}</h2>
        <StatusPill status={document.status} />
      </div>

      {!reason && matches?.destination === destination && (
        <AlreadyThere matches={matches} label={label} names={RECORD_NAMES[destination] || RECORD_NAMES.hubspot} />
      )}
      {crm && savedIn !== destination && document.can_sync && !reason && (
        <p className="hint">Sent to {DESTINATION_LABELS[savedIn]} before. Sending now adds it to {label} as well.</p>
      )}
      <button type="button" className="button primary wide" onClick={onSend} disabled={Boolean(reason) || sending || !document.can_sync}>
        {sending || document.status === 'syncing' ? 'Sending...' : document.status === 'sync_failed' ? 'Try again' : duplicateDeal && !reason ? 'Send anyway' : `Send to ${label}`}
      </button>
      <p className="hint" aria-live="polite">{reason}</p>
      {user?.practice_sends && document.destination && (
        <p className="hint">This is a practice account: sending shows what {label} would get, but nothing leaves Inkfold.</p>
      )}
      {error && <p className="banner error" role="alert">{error}</p>}
      {document.status === 'sync_failed' && document.error && !error && <p className="banner error">{document.error}</p>}

      {crm && (
        <div className="crm">
          <p className="crm-title">
            {crm.simulated
              ? `Practice records (not in ${DESTINATION_LABELS[savedIn]})`
              : savedIn === 'sheets' ? 'In the sheet' : `Records in ${DESTINATION_LABELS[savedIn]}`}
          </p>
          <dl>
            {[[names[0], crm.contact_id], [names[1], crm.company_id], [names[2], crm.deal_id], [names[3], crm.note_id], ['Row', crm.row]]
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
                <span className="event-what">
                  {EVENT_LABELS[event.status] || event.status} · {CHANNELS[event.channel] || event.channel}
                  {inTeam(user) && event.actor && <span className="event-who"> · by {who(event.actor, user)}</span>}
                </span>
                {event.detail && <span className="event-detail">{event.detail}</span>}
              </li>
            ))}
          </ol>
        </div>
      )}
    </aside>
  )
}
