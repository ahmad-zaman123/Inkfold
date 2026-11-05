import { Link } from 'react-router-dom'
import ThemeToggle from './ThemeToggle.jsx'

export function Mark() {
  return (
    <svg className="mark" viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="7" />
      <path d="M10 8h12M10 14h8M10 20h12" />
      <circle cx="22" cy="14" r="2.4" />
    </svg>
  )
}

export default function Header({ user, onSignOut }) {
  return (
    <header className="topbar">
      <Link to="/" className="brand" aria-label="Inkfold home">
        <Mark />
        <span className="brand-name">Inkfold</span>
      </Link>
      <nav className="topbar-right" aria-label="Account">
        {user && (
          <>
            <span className="who">{user.is_demo ? 'Demo account' : user.email}</span>
            <Link to="/team" className="link-button">
              Team
            </Link>
            {!user.is_demo && (
              <Link to="/account" className="link-button">
                Account
              </Link>
            )}
            <button type="button" className="link-button" onClick={onSignOut}>
              Sign out
            </button>
          </>
        )}
        {!user && (
          <Link to="/login" className="link-button">
            Sign in
          </Link>
        )}
        <ThemeToggle />
      </nav>
    </header>
  )
}
