'use client'

import { usePathname } from 'next/navigation'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useAuth } from '../lib/auth-context'
import { displayFont, monoFont } from '../lib/fonts'
import { NavLink } from './NavLink'

const fontVars = `${displayFont.variable} ${monoFont.variable}`

const NAV_GROUPS: [string, [string, string][]][] = [
  ['Explore', [['/', 'Overview'], ['/search', 'Search'], ['/graph', 'Graph'], ['/events', 'Events'], ['/map', 'Map'], ['/compare', 'Compare']]],
  ['Archive', [['/sources', 'Sources'], ['/articles', 'Articles']]],
  ['Investigations', [['/saved-searches', 'Saved Searches'], ['/monitors', 'Watchlist']]],
  ['System', [['/jobs', 'Jobs'], ['/operations', 'Operations'], ['/settings', 'Settings']]],
]

export function Shell({ children }: { children: ReactNode }) {
  const { user, error, signIn, signOut } = useAuth()
  const pathname = usePathname()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)
  const menuButton = useRef<HTMLButtonElement>(null)

  useEffect(() => { setMenuOpen(false) }, [pathname])

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

  function closeMenu({ restoreFocus }: { restoreFocus: boolean }) {
    setMenuOpen(false)
    if (restoreFocus) menuButton.current?.focus()
  }

  return (
    /* fontVars only defines --font-display/--font-mono-data as CSS custom properties here (inherited
       by main's children too, for Overview/Search to opt into); it does not itself set
       font-family, so the un-redesigned routes keep inheriting the legacy Inter body font. */
    <div className={`${fontVars} min-h-screen lg:grid lg:grid-cols-[240px_minmax(0,1fr)]`}>
      <a href="#main" className="sr-only rounded-lg bg-primary px-4 py-2 font-sans text-sm font-semibold text-primary-foreground focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50">Skip to content</a>
      <aside
        className="flex flex-col gap-4 border-b border-border bg-background/80 p-4 font-sans backdrop-blur-xl lg:sticky lg:top-0 lg:max-h-dvh lg:overflow-y-auto lg:border-r lg:border-b-0 lg:p-6"
        onKeyDown={event => { if (event.key === 'Escape' && menuOpen) closeMenu({ restoreFocus: true }) }}
      >
        <div className="flex items-center justify-between gap-4">
          <h1 className="font-sans text-[22px] font-extrabold tracking-tight text-foreground">NewsIntel</h1>
          <button
            ref={menuButton}
            type="button"
            className="w-auto rounded-lg border border-border bg-transparent px-3 py-1.5 font-sans text-sm font-medium text-muted-foreground hover:border-ring hover:text-foreground lg:hidden"
            aria-expanded={menuOpen}
            aria-controls="main-navigation"
            onClick={() => setMenuOpen(open => !open)}
          >
            Menu
          </button>
        </div>
        {/* Closed on narrow screens means display:none, which also takes the links out of the tab order. */}
        <div id="main-navigation" className={`${menuOpen ? 'flex' : 'hidden'} flex-col gap-4 lg:flex`}>
          <nav
            aria-label="Main navigation"
            className="flex flex-col gap-4"
            onClick={event => { if ((event.target as HTMLElement).closest('a')) closeMenu({ restoreFocus: false }) }}
          >
            {NAV_GROUPS.map(([group, links]) => (
              <div key={group} className="flex flex-col gap-1">
                <p className="px-3 text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground/70">{group}</p>
                {links.map(([href, label]) => <NavLink key={href} href={href}>{label}</NavLink>)}
              </div>
            ))}
          </nav>
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-4">
            <span className="truncate text-sm text-muted-foreground" title={user.username}>{user.username}</span>
            <button
              type="button"
              className="w-auto rounded-lg border border-border bg-transparent px-3 py-1.5 font-sans text-sm text-muted-foreground transition-colors hover:border-ring hover:text-foreground"
              onClick={handleSignOut}
            >
              Sign out
            </button>
          </div>
        </div>
      </aside>
      <main id="main" tabIndex={-1} className="min-w-0 p-4 outline-none sm:p-6 lg:p-8">{children}</main>
    </div>
  )
}
