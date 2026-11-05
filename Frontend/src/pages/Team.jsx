import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { api } from '../api.js'
import { formatDay } from '../labels.js'

function CopyLink({ link }) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(link)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }
  return (
    <div className="copy-link">
      <input readOnly value={link} aria-label="Invite link" onFocus={(e) => e.target.select()} />
      <button type="button" className="button" onClick={copy}>{copied ? 'Copied' : 'Copy link'}</button>
    </div>
  )
}

const DESTINATION_HINTS = {
  hubspot: 'A contact, company, deal and note.',
  pipedrive: 'A person, organization, deal and note.',
  sheets: 'One row per document. Good for a team without a CRM.',
}

export default function Team({ user, onChanged }) {
  const [team, setTeam] = useState(null)
  const [error, setError] = useState('')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState(null)
  const { state } = useLocation()
  const [notice, setNotice] = useState(() => {
    if (state?.joined === undefined) return ''
    const moved = state.joined ? ` ${state.joined === 1 ? '1 of your documents' : `${state.joined} of your documents`} came with you.` : ''
    return `You joined the team.${moved}`
  })

  function show(next) {
    setTeam(next)
    setName(next.name)
  }

  useEffect(() => {
    api.team().then(show).catch((err) => setError(err.message))
  }, [])

  // Every change to the team is one request, so this wraps them with the same busy state and errors.
  async function run(action, after) {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const next = await action()
      show(next)
      after?.(next)
      onChanged()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (!team) {
    return (
      <div className="page narrow">
        {error ? <p className="banner error" role="alert">{error}</p> : <p className="muted" aria-busy="true">Loading...</p>}
      </div>
    )
  }

  const owner = team.role === 'owner'
  const others = team.members.filter((m) => !m.you)

  return (
    <div className="page narrow team">
      <div className="page-head">
        <h1>Team</h1>
        <p className="lede small">
          Everyone in the team sees the same documents, can review them, and sends them to the same place.
        </p>
      </div>
      {error && <p className="banner error" role="alert">{error}</p>}
      {notice && <p className="banner success" role="status">{notice}</p>}
      {user.is_demo && (
        <p className="banner info">The demo is private to you. <Link to="/register">Create an account</Link> to invite teammates.</p>
      )}

      {owner && !user.is_demo ? (
        <form
          className="card team-section"
          onSubmit={(e) => {
            e.preventDefault()
            run(() => api.renameTeam(name), () => setNotice('Team name saved.'))
          }}
        >
          <label className="field-label" htmlFor="team-name">Team name</label>
          <div className="inline-form">
            <input id="team-name" value={name} maxLength={80} placeholder="Such as Northwind Sales" onChange={(e) => setName(e.target.value)} />
            <button type="submit" className="button" disabled={busy || name === team.name}>Save</button>
          </div>
        </form>
      ) : (
        team.name && <p className="team-title">{team.name}</p>
      )}

      <section className="card team-section" aria-labelledby="destination-heading">
        <h2 id="destination-heading">Send documents to</h2>
        {team.destinations.length === 0 && <p className="muted">Nothing is connected yet. Set up HubSpot, Pipedrive or Google Sheets on the server.</p>}
        {team.destinations.length > 0 && (
          <div className="destination-options" role="radiogroup" aria-label="Send documents to">
            {team.destinations.map((option) => (
              <label key={option.key} className={`destination-option${team.destination === option.key ? ' chosen' : ''}`}>
                <input
                  type="radio"
                  name="destination"
                  value={option.key}
                  checked={team.destination === option.key}
                  disabled={!owner || busy}
                  onChange={() => run(() => api.chooseDestination(option.key), () => setNotice(`Documents now go to ${option.label}.`))}
                />
                <span className="destination-name">{option.label}</span>
                <span className="destination-hint">{DESTINATION_HINTS[option.key]}</span>
              </label>
            ))}
          </div>
        )}
        {!owner && <p className="hint">The team's owner chooses this.</p>}
      </section>

      <section className="card team-section" aria-labelledby="members-heading">
        <h2 id="members-heading">People</h2>
        <ul className="member-list">
          {team.members.map((member) => (
            <li key={member.id}>
              <span className="member-email">
                {member.email}
                {member.you && <span className="muted"> (you)</span>}
              </span>
              <span className="member-role">{member.role === 'owner' ? 'Owner' : 'Reviewer'}</span>
              {owner && !member.you && (
                <button
                  type="button"
                  className="link-button danger"
                  disabled={busy}
                  onClick={() =>
                    window.confirm(`Remove ${member.email}? They lose access to the team's documents. Anything they added stays.`) &&
                    run(() => api.removeMember(member.id), () => setNotice(`${member.email} was removed.`))
                  }
                >
                  Remove
                </button>
              )}
            </li>
          ))}
        </ul>
        {!owner && (
          <button
            type="button"
            className="button subtle"
            disabled={busy}
            onClick={() =>
              window.confirm("Leave this team? You'll lose access to its documents. Anything you added stays with the team.") &&
              run(() => api.leaveTeam(), () => setNotice('You left the team. Your documents list starts empty.'))
            }
          >
            Leave team
          </button>
        )}
      </section>

      {team.can_invite && (
        <section className="card team-section" aria-labelledby="invite-heading">
          <h2 id="invite-heading">Invite a teammate</h2>
          <form
            className="inline-form"
            onSubmit={(e) => {
              e.preventDefault()
              run(() => api.invite(email), (next) => {
                setSent(next.invite)
                setEmail('')
              })
            }}
          >
            <label className="visually-hidden" htmlFor="invite-email">Their email</label>
            <input id="invite-email" type="email" required placeholder="name@company.com" value={email} onChange={(e) => setEmail(e.target.value)} />
            <button type="submit" className="button primary" disabled={busy}>
              {team.email_invites ? 'Send invite' : 'Create invite link'}
            </button>
          </form>
          <p className="hint">
            They sign in or create an account with that email to join. Links expire after 7 days and work once.
          </p>
          {sent && (
            <div className="invite-sent" role="status">
              <p>
                {sent.emailed
                  ? <>Invite emailed to <strong>{sent.email}</strong>. You can also send them this link:</>
                  : <>Send this link to <strong>{sent.email}</strong>:</>}
              </p>
              <CopyLink link={sent.link} />
            </div>
          )}
          {team.invites.length > 0 && (
            <>
              <h3 className="crm-title">Waiting to join</h3>
              <ul className="member-list">
                {team.invites.map((invite) => (
                  <li key={invite.id}>
                    <span className="member-email">{invite.email}</span>
                    <span className="member-role">Expires {formatDay(invite.expires_at)}</span>
                    <button
                      type="button"
                      className="link-button danger"
                      disabled={busy}
                      onClick={() => run(() => api.cancelInvite(invite.id), () => sent?.id === invite.id && setSent(null))}
                    >
                      Cancel
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}
      {owner && others.length === 0 && !user.is_demo && (
        <p className="muted small-note">Only you so far. Documents you add now are shared once someone joins.</p>
      )}
    </div>
  )
}
