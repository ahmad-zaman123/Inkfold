import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api.js'

export default function JoinTeam({ user, onJoined, onSignOut }) {
  const { token } = useParams()
  const navigate = useNavigate()
  const [invite, setInvite] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.inviteInfo(token).then(setInvite).catch((err) => setError(err.message))
  }, [token])

  async function accept() {
    setBusy(true)
    setError('')
    try {
      const result = await api.acceptInvite(token)
      await onJoined()
      navigate('/team', { replace: true, state: { joined: result.moved } })
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  const here = `/join/${token}`
  const back = `?next=${encodeURIComponent(here)}&email=${encodeURIComponent(invite?.email || '')}`
  const matches = user && invite && user.email === invite.email && !user.is_demo
  const alone = (user?.team?.size ?? 1) === 1

  return (
    <div className="auth">
      <div className="card auth-card">
        <h1>Join a team</h1>
        {!invite && !error && <p className="muted" aria-busy="true">Checking the invite...</p>}
        {error && <p className="banner error" role="alert">{error}</p>}
        {invite && (
          <>
            <p>
              <strong>{invite.invited_by || 'Someone'}</strong> invited <strong>{invite.email}</strong> to join{' '}
              {invite.team ? <strong>{invite.team}</strong> : 'their team'} on Inkfold
              {invite.team_size > 1 ? `, with ${invite.team_size} people in it` : ''}.
            </p>
            {!user && (
              <>
                <p className="muted">Sign in or create an account as {invite.email} to accept.</p>
                <Link className="button primary wide" to={`/login${back}`}>Sign in</Link>
                <p className="switch"><Link to={`/register${back}`}>Create an account</Link></p>
              </>
            )}
            {user && !matches && (
              <>
                <p className="banner warn">
                  {user.is_demo ? "You're using the demo." : <>You're signed in as <strong>{user.email}</strong>.</>} This invite is
                  for {invite.email}, so sign in with that address to accept it.
                </p>
                <button type="button" className="button wide" onClick={() => onSignOut(`/login${back}`)}>Sign out and switch</button>
              </>
            )}
            {matches && (
              <>
                <p className="muted">
                  {alone
                    ? "Your documents come with you. Any file the team already has isn't added twice."
                    : "You'll leave your current team. The documents there stay with it."}
                </p>
                <button type="button" className="button primary wide" disabled={busy} onClick={accept}>
                  {busy ? 'Joining...' : invite.team ? `Join ${invite.team}` : 'Join the team'}
                </button>
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}
