'use client'

import { useState, type ReactNode } from 'react'
import { useAuth } from '../lib/auth-context'
import { NavLink } from './NavLink'

export function Shell({ children }: { children: ReactNode }) {
  const { user, error, signIn, signOut } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  if (!user) {
    return (
      <main className="login-page">
        <section className="login-card">
          <p className="eyebrow">Self-hosted news intelligence</p>
          <h1>NewsIntel</h1>
          <p className="lede">Your archive, investigations, and operational picture in one place.</p>
          <form onSubmit={event => { event.preventDefault(); void signIn(username, password) }}>
            <label>Username<input value={username} onChange={event => setUsername(event.target.value)} autoComplete="username" required /></label>
            <label>Password<input value={password} onChange={event => setPassword(event.target.value)} type="password" autoComplete="current-password" required minLength={12} /></label>
            {error && <p role="alert" className="error">{error}</p>}
            <button type="submit">Sign in</button>
          </form>
        </section>
      </main>
    )
  }

  async function handleSignOut() {
    await signOut()
    setPassword('')
  }

  return (
    <div className="shell">
      <aside>
        <h1>NewsIntel</h1>
        <nav aria-label="Main navigation">
          <NavLink href="/">Overview</NavLink>
          <NavLink href="/sources">Sources</NavLink>
          <NavLink href="/articles">Articles</NavLink>
          <NavLink href="/search">Search</NavLink>
          <NavLink href="/graph">Graph</NavLink>
          <NavLink href="/saved-searches">Saved Searches</NavLink>
          <NavLink href="/jobs">Jobs</NavLink>
          <NavLink href="/settings">Settings</NavLink>
        </nav>
        <button className="secondary signout" onClick={handleSignOut}>Sign out</button>
      </aside>
      <main className="dashboard">{children}</main>
    </div>
  )
}
