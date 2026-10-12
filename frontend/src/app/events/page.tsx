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
import { entityHref, eventHref, mapHref, toHref } from '../../lib/investigation'
import { countryLabel } from '../../lib/countries'
import { CountrySelect } from '../../components/SearchablePicker'
import { when } from '../../lib/datetime'

const STATUSES = ['active', 'closed', 'superseded'] as const
// Fixed order keeps a filtered list's URL stable and bookmarkable.
const KEYS = ['q', 'status', 'country', 'entity_id', 'from', 'to', 'sort', 'all'] as const
type Key = typeof KEYS[number]
const DAY = /^\d{4}-\d{2}-\d{2}$/

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
    ...(value('q').trim() && { q: value('q').trim() }),
    ...(STATUSES.includes(value('status') as typeof STATUSES[number]) && { status: value('status') }),
    ...(value('country').length === 2 && { country: value('country').toUpperCase() }),
    ...(value('entity_id') && { entity_id: value('entity_id') }),
    ...(day('from') && { from: `${day('from')}T00:00:00Z` }),
    ...(day('to') && { to: `${day('to')}T23:59:59Z` }),
    // One-story events repeat the stories list, so they stay hidden until asked for.
    ...(value('all') !== '1' && { min_stories: '2' }),
    ...(value('sort') === 'biggest' && { sort: 'biggest' }),
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
          <form role="search" aria-label="Search events" className="flex items-end gap-2" onSubmit={event => {
            event.preventDefault()
            setFilter('q', String(new FormData(event.currentTarget).get('q') ?? '').trim())
          }}>
            <input key={value('q')} className={fieldClass} type="search" name="q" aria-label="Search events" placeholder="Words in a headline or a name" defaultValue={value('q')} maxLength={200} />
            <button className={ghostButtonClass} type="submit">Search</button>
          </form>
          <label className={labelClass}>Status
            <select className={fieldClass} value={value('status')} onChange={event => setFilter('status', event.target.value)}>
              <option value="">Any status</option>
              {STATUSES.map(status => <option key={status} value={status}>{status}</option>)}
            </select>
          </label>
          <CountrySelect label="Country" value={value('country').toUpperCase()} emptyLabel="Any country" onChange={code => setFilter('country', code)} />
          {value('country').length === 2 && <Link className={ghostButtonClass} href={mapHref({ role: 'event', country: value('country') })}>View on map</Link>}
          <label className={labelClass}>From
            <input className={fieldClass} type="date" value={value('from')} onChange={event => setFilter('from', event.target.value)} />
          </label>
          <label className={labelClass}>To
            <input className={fieldClass} type="date" value={value('to')} onChange={event => setFilter('to', event.target.value)} />
          </label>
          <label className={labelClass}>Sort
            <select className={fieldClass} value={value('sort') === 'biggest' ? 'biggest' : ''} onChange={event => setFilter('sort', event.target.value)}>
              <option value="">Latest first</option>
              <option value="biggest">Biggest first</option>
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            <input type="checkbox" checked={value('all') === '1'} onChange={event => setFilter('all', event.target.checked ? '1' : '')} />
            Show one-story events
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
        {events.data && !items.length && (value('all') === '1'
          ? <Note>No events match these filters.</Note>
          : <div className="flex flex-wrap items-center gap-3 px-6 py-4 text-sm text-muted-foreground">
              <span>No events with two or more stories match these filters.</span>
              <button className={ghostButtonClass} onClick={() => setFilter('all', '1')}>Show one-story events</button>
            </div>)}
        {items.map(event => (
          <div key={event.id} className="flex flex-col gap-1.5 border-border px-6 py-4">
            <div className="flex items-start justify-between gap-3">
              <Link className="text-[15px] font-semibold text-foreground hover:underline" href={eventHref(event.id, currentHref)}>{event.headline ?? 'Untitled event'}</Link>
              <StatusBadge tone={event.status === 'active' ? 'healthy' : 'pending'}>{event.status}</StatusBadge>
            </div>
            <p className="text-xs text-muted-foreground">{when(event.started_at)} – {when(event.ended_at)}{event.primary_country ? ` · ${countryLabel(event.primary_country)}` : ''}</p>
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
