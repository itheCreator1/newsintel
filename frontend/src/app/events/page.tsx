'use client'

import { useInfiniteQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense } from 'react'
import { api, type EventFilters } from '../../lib/api'
import { GlassPanel } from '../../components/GlassPanel'
import { PageHeader } from '../../components/PageHeader'
import { StatusBadge } from '../../components/StatusBadge'
import { plural } from '../../lib/utils'
import { chipClass, fieldClass, ghostButtonClass, labelClass } from '../../lib/ui-classes'
import { entityHref, eventHref, toHref } from '../../lib/investigation'

const STATUSES = ['active', 'closed', 'superseded'] as const
// Fixed order keeps a filtered list's URL stable and bookmarkable.
const KEYS = ['status', 'country', 'entity_id', 'from', 'to'] as const
type Key = typeof KEYS[number]
const DAY = /^\d{4}-\d{2}-\d{2}$/
const when = (value: string | null | undefined) => value ? new Date(value).toLocaleString() : '—'

function Note({ children, error }: { children: string; error?: boolean }) {
  return <p className={error ? 'error px-6 py-4 text-sm text-destructive' : 'px-6 py-4 text-sm text-muted-foreground'}>{children}</p>
}

function EventsContent() {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const currentHref = toHref(pathname, searchParams)
  const value = (key: Key) => searchParams.get(key) ?? ''
  const day = (key: 'from' | 'to') => DAY.test(value(key)) ? value(key) : ''

  // A whole-day range: `to` means the end of that UTC day, not its first instant.
  const filters: EventFilters = {
    ...(STATUSES.includes(value('status') as typeof STATUSES[number]) && { status: value('status') }),
    ...(value('country').length === 2 && { country: value('country').toUpperCase() }),
    ...(value('entity_id') && { entity_id: value('entity_id') }),
    ...(day('from') && { from: `${day('from')}T00:00:00Z` }),
    ...(day('to') && { to: `${day('to')}T23:59:59Z` }),
  }
  const events = useInfiniteQuery({
    queryKey: ['events', filters], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => api.events(filters, pageParam), getNextPageParam: page => page.next_cursor ?? undefined, retry: false,
  })
  const items = events.data?.pages.flatMap(page => page.items) ?? []

  function setFilter(key: Key, next: string) {
    const query = new URLSearchParams()
    for (const name of KEYS) {
      const current = name === key ? next : value(name)
      if (current) query.set(name, current)
    }
    router.replace(toHref(pathname, query))
  }

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="Events" title="Events">
        <div className="flex flex-wrap items-end gap-3">
          <label className={labelClass}>Status
            <select className={fieldClass} value={value('status')} onChange={event => setFilter('status', event.target.value)}>
              <option value="">Any status</option>
              {STATUSES.map(status => <option key={status} value={status}>{status}</option>)}
            </select>
          </label>
          <label className={labelClass}>Country
            <input className={fieldClass} value={value('country')} maxLength={2} placeholder="GR" onChange={event => setFilter('country', event.target.value.toUpperCase())} />
          </label>
          <label className={labelClass}>From
            <input className={fieldClass} type="date" value={value('from')} onChange={event => setFilter('from', event.target.value)} />
          </label>
          <label className={labelClass}>To
            <input className={fieldClass} type="date" value={value('to')} onChange={event => setFilter('to', event.target.value)} />
          </label>
        </div>
      </PageHeader>

      {value('entity_id') && (
        <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <span>Filtered by entity</span>
          <Link className={chipClass} href={entityHref(value('entity_id'))}>Open entity</Link>
          <button className={ghostButtonClass} onClick={() => setFilter('entity_id', '')}>Clear entity filter</button>
        </div>
      )}

      <GlassPanel className="overflow-hidden p-0">
        {events.isPending && <Note>Loading events…</Note>}
        {events.isError && <Note error>Could not load events.</Note>}
        {events.data && !items.length && <Note>No events match these filters.</Note>}
        {items.map(event => (
          <div key={event.id} className="flex flex-col gap-1.5 border-border px-6 py-4">
            <div className="flex items-start justify-between gap-3">
              <Link className="text-[15px] font-semibold text-foreground hover:underline" href={eventHref(event.id, currentHref)}>{event.headline ?? 'Untitled event'}</Link>
              <StatusBadge tone={event.status === 'active' ? 'healthy' : 'pending'}>{event.status}</StatusBadge>
            </div>
            <p className="text-xs text-muted-foreground">{when(event.started_at)} – {when(event.ended_at)}{event.primary_country ? ` · ${event.primary_country}` : ''}</p>
            <p className="text-sm text-foreground">{`${plural(event.cluster_count, 'story', 'stories')} · ${plural(event.article_count, 'article')} · ${plural(event.source_count, 'source')}`}</p>
            {event.entities.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {event.entities.map(entity => <Link key={entity.id} className={chipClass} href={entityHref(entity.id)}>{entity.display_name}</Link>)}
              </div>
            )}
          </div>
        ))}
        {events.hasNextPage && <div className="px-6 py-4"><button className={ghostButtonClass} disabled={events.isFetchingNextPage} onClick={() => events.fetchNextPage()}>{events.isFetchingNextPage ? 'Loading…' : 'Load more events'}</button></div>}
      </GlassPanel>
    </div>
  )
}

export default function EventsPage() {
  return <Suspense fallback={null}><EventsContent /></Suspense>
}
