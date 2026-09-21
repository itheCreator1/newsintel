'use client'

import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { monitorKeys, refetchEvery, unseenMonitors } from '../lib/monitors'
import { NavLink } from './NavLink'

/** The Watchlist nav link, with the number of monitors that have something new; nothing while loading or on error. */
export function WatchlistLink({ href, label }: { href: string; label: string }) {
  // ponytail: counts the first 100 monitors; a /monitors/summary count endpoint if a user ever has more.
  const monitors = useQuery({ queryKey: monitorKeys.badge, queryFn: () => api.monitors('activity', undefined, 100), retry: false, refetchInterval: query => refetchEvery(query.state.data?.items) })
  const count = unseenMonitors(monitors.data?.items)
  if (!count) return <NavLink href={href}>{label}</NavLink>
  return (
    <NavLink href={href} aria-label={`${label}, ${count} with new results`} className="flex items-center justify-between gap-2">
      {label}
      <span aria-hidden="true" className="rounded-full bg-primary px-2 py-0.5 text-[11px] font-semibold leading-none text-primary-foreground">{count > 99 ? '99+' : count}</span>
    </NavLink>
  )
}
