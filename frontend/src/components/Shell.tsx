'use client'

import { useState, type ReactNode } from 'react'
import { useAuth } from '../lib/auth-context'
import { displayFont, monoFont } from '../lib/fonts'
import { NavLink } from './NavLink'

const fontVars = `${displayFont.variable} ${monoFont.variable}`

export function Shell({ children }: { children: ReactNode }) {
  const { user, error, signIn, signOut } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  if (!user) {
    return (
      <main className={`${fontVars} login-page font-sans`}>
        <section className="login-card w-full max-w-[440px] rounded-3xl border border-border bg-card/70 p-10 shadow-glow-sm backdrop-blur-xl">
          <p className="eyebrow text-primary/80">Self-hosted news intelligence</p>
          <h1 className="font-sans text-[38px] font-extrabold tracking-tight text-foreground">NewsIntel</h1>
          <p className="lede text-muted-foreground">Your archive, investigations, and operational picture in one place.</p>
          <form onSubmit={event => { event.preventDefault(); void signIn(username, password) }}>
            <label className="text-muted-foreground">Username
              <input
                className="rounded-xl border border-border bg-background/60 text-foreground outline-none focus:border-ring focus:ring-2 focus:ring-ring/40"
                value={username} onChange={event => setUsername(event.target.value)} autoComplete="username" required
              />
            </label>
            <label className="text-muted-foreground">Password
              <input
                className="rounded-xl border border-border bg-background/60 text-foreground outline-none focus:border-ring focus:ring-2 focus:ring-ring/40"
                value={password} onChange={event => setPassword(event.target.value)} type="password" autoComplete="current-password" required minLength={12}
              />
            </label>
            {error && <p role="alert" className="error text-destructive">{error}</p>}
            <button type="submit" className="rounded-xl bg-primary font-sans text-primary-foreground shadow-glow-sm transition-shadow hover:shadow-glow">Sign in</button>
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
    /* fontVars only defines --font-display/--font-mono-data as CSS custom properties here (inherited
       by .dashboard's children too, for Overview/Search to opt into); it does not itself set
       font-family, so the seven un-redesigned routes keep inheriting the legacy Inter body font. */
    <div className={`${fontVars} shell`}>
      <aside className="border-r border-border bg-background/80 font-sans backdrop-blur-xl">
        <h1 className="font-sans text-[22px] font-extrabold tracking-tight text-foreground">NewsIntel</h1>
        <nav aria-label="Main navigation" className="flex flex-col gap-1">
          <NavLink href="/">Overview</NavLink>
          <NavLink href="/sources">Sources</NavLink>
          <NavLink href="/articles">Articles</NavLink>
          <NavLink href="/search">Search</NavLink>
          <NavLink href="/graph">Graph</NavLink>
          <NavLink href="/events">Events</NavLink>
          <NavLink href="/map">Map</NavLink>
          <NavLink href="/compare">Compare</NavLink>
          <NavLink href="/saved-searches">Saved Searches</NavLink>
          <NavLink href="/monitors">Watchlist</NavLink>
          <NavLink href="/jobs">Jobs</NavLink>
          <NavLink href="/settings">Settings</NavLink>
        </nav>
        <button
          className="secondary signout rounded-lg border border-border bg-transparent font-sans text-sm text-muted-foreground transition-colors hover:border-ring hover:text-foreground"
          onClick={handleSignOut}
        >
          Sign out
        </button>
      </aside>
      <main className="dashboard">{children}</main>
    </div>
  )
}
