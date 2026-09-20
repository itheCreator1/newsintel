'use client'

import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense } from 'react'
import { api } from '../../lib/api'
import type { GeoArticleRole, GeoCountriesResponse, GeoRole } from '../../lib/api-types'
import { GeoChart } from '../../components/GeoChart'
import { GlassPanel } from '../../components/GlassPanel'
import { PageHeader } from '../../components/PageHeader'
import world from '../../lib/world.geo.json'
import { compareHref, emptyInvestigation, eventHref, mapHref, queryFromState, refine, toHref, type ListField } from '../../lib/investigation'
import { countryName, plural } from '../../lib/utils'
import { fieldClass, ghostButtonClass, labelClass } from '../../lib/ui-classes'

// One role per map: the four location roles mean different things and are never added together.
const ROLES: { value: GeoRole; label: string; note: string }[] = [
  { value: 'story', label: 'Story country', note: 'The one country an article is about: named alone in its title and repeated in its text. Only some English-language articles have one.' },
  { value: 'mentioned', label: 'Mentioned country', note: 'Every country named in an article’s text. One article can appear under several countries.' },
  { value: 'source', label: 'Source country', note: 'The country set on the feed that published the article: where it was published, not what it is about.' },
  { value: 'event', label: 'Event country', note: 'An event’s most frequent story country. It is derived from the story country, not a separate location.' },
]
const WINDOWS = [7, 30, 90, 365]
const COUNTRY = /^[A-Za-z]{2}$/
const DRAWN = new Set(world.features.map(feature => feature.properties.code))
const SEARCH_FIELD: Record<GeoArticleRole, ListField> = { story: 'story_country', mentioned: 'mentioned_country', source: 'source_country' }
const when = (value: string | null | undefined) => value ? new Date(value).toLocaleString() : '—'
const pageOf = <T extends { next_cursor: string | null }>(load: (cursor?: string) => Promise<T>) => ({
  initialPageParam: undefined as string | undefined,
  queryFn: ({ pageParam }: { pageParam: string | undefined }) => load(pageParam),
  getNextPageParam: (page: T) => page.next_cursor ?? undefined,
  retry: false,
})

function Note({ children, error }: { children: string; error?: boolean }) {
  return <p className={error ? 'error px-6 py-4 text-sm text-destructive' : 'px-6 py-4 text-sm text-muted-foreground'}>{children}</p>
}

function coverageText({ coverage }: GeoCountriesResponse, role: GeoRole, days: number) {
  const { unit, window_total: total, located } = coverage
  const noun = unit === 'events' ? 'events' : 'articles'
  const kind = unit === 'events' ? 'a country' : `a ${role} country`
  if (!total) return `No ${noun} in the last ${days} days.`
  if (!located) return `None of the ${total} ${noun} in the last ${days} days has ${kind}.`
  return `${located} of ${total} ${noun} in the last ${days} days have ${kind}; ${total - located} have none.`
}

function MapContent() {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const currentHref = toHref(pathname, searchParams)
  const role = ROLES.find(item => item.value === searchParams.get('role'))?.value ?? 'story'
  const days = WINDOWS.includes(Number(searchParams.get('days'))) ? Number(searchParams.get('days')) : 30
  const country = COUNTRY.test(searchParams.get('country') ?? '') ? searchParams.get('country')!.toUpperCase() : ''
  const definition = ROLES.find(item => item.value === role)!

  const map = useQuery({ queryKey: ['geo-countries', role, days], queryFn: () => api.geoCountries(role, days), retry: false })
  const data = map.data
  const found = data?.items.find(item => item.country_code === country)
  const start = data?.window_start ?? ''
  const from = start.slice(0, 10)
  const articleRole = role === 'event' ? undefined : role
  const articles = useInfiniteQuery({ queryKey: ['geo-articles', role, country, days], ...pageOf(cursor => api.geoArticles(articleRole!, country, days, cursor)), enabled: Boolean(found && articleRole) })
  const events = useInfiniteQuery({ queryKey: ['geo-events', country, start], ...pageOf(cursor => api.events({ country, from: start }, cursor)), enabled: Boolean(found && !articleRole) })

  const value = (item: GeoCountriesResponse['items'][number]) => item.events ?? item.articles ?? 0
  const unit = role === 'event' ? 'events' : 'articles'
  const go = (next: { role?: GeoRole; days?: number; country?: string }) => router.replace(mapHref({ role, days, country, ...next }))
  const toggle = (code: string) => go({ country: code === country ? '' : code })
  const name = country ? countryName(country) : ''
  const openArticle = (id: string) => toHref('/articles', new URLSearchParams({ article: id, from: currentHref }))
  const articleItems = articles.data?.pages.flatMap(page => page.items) ?? []
  const eventItems = events.data?.pages.flatMap(page => page.items) ?? []

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="Map" title="Map">
        <label className={labelClass}>Location role
          <select className={fieldClass} value={role} onChange={event => go({ role: event.target.value as GeoRole })}>
            {ROLES.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
        </label>
        <label className={labelClass}>Window
          <select className={fieldClass} value={days} onChange={event => go({ days: Number(event.target.value) })}>
            {WINDOWS.map(option => <option key={option} value={option}>Last {option} days</option>)}
          </select>
        </label>
      </PageHeader>

      <GlassPanel className="flex flex-col gap-2">
        <p className="text-sm text-foreground">{definition.note}</p>
        <p className="text-xs text-muted-foreground">An article with several countries counts once under each. Roles are never added together, so switch roles to compare them.</p>
        {data && <p className="text-sm text-foreground">{coverageText(data, role, days)}</p>}
      </GlassPanel>

      {map.isPending && <GlassPanel className="p-0"><Note>Loading the map…</Note></GlassPanel>}
      {map.isError && <GlassPanel className="p-0"><Note error>Could not load the map.</Note></GlassPanel>}

      {data && data.items.length > 0 && (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          <GlassPanel>
            <GeoChart
              items={data.items.filter(item => DRAWN.has(item.country_code)).map(item => ({ code: item.country_code, label: countryName(item.country_code), value: value(item) }))}
              selected={country || undefined} unit={unit} onSelect={toggle}
              ariaLabel={`Map of ${unit} by ${definition.label.toLowerCase()}. Use the table to select a country.`} />
          </GlassPanel>
          <GlassPanel className="overflow-x-auto p-0">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="text-xs text-muted-foreground">
                  <th className="px-4 py-2 font-medium">Country</th>
                  {role === 'event' ? <th className="py-2 pr-4 font-medium">Events</th> : <><th className="py-2 pr-4 font-medium">Articles</th><th className="py-2 pr-4 font-medium">Stories</th></>}
                  {role === 'source' && <th className="py-2 pr-4 font-medium">Sources</th>}
                </tr>
              </thead>
              <tbody>
                {data.items.map(item => (
                  <tr key={item.country_code} className="border-t border-border">
                    <td className="px-4 py-1.5">
                      <button className="text-left text-foreground hover:underline" aria-pressed={item.country_code === country} onClick={() => toggle(item.country_code)}>{countryName(item.country_code)}</button>
                      {!DRAWN.has(item.country_code) && <span className="ml-2 text-xs text-muted-foreground">not drawn on the map</span>}
                    </td>
                    {role === 'event' ? <td className="py-1.5 pr-4 text-foreground">{item.events}</td> : <><td className="py-1.5 pr-4 text-foreground">{item.articles}</td><td className="py-1.5 pr-4 text-foreground">{item.stories}</td></>}
                    {role === 'source' && <td className="py-1.5 pr-4 text-foreground">{item.sources}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </GlassPanel>
        </div>
      )}

      {data && country && (
        <GlassPanel aria-label={name} className="flex flex-col gap-3 p-0">
          <div className="flex flex-wrap items-start justify-between gap-3 px-6 pt-6">
            <div className="flex flex-col gap-1">
              <h3 className="text-lg font-semibold text-foreground">{name}</h3>
              {found
                ? <p className="text-sm text-muted-foreground">{role === 'event' ? plural(found.events ?? 0, 'event') : `${plural(found.articles ?? 0, 'article')} · ${plural(found.stories ?? 0, 'story', 'stories')}${role === 'source' ? ` · ${plural(found.sources ?? 0, 'source')}` : ''}`}</p>
                : <p className="text-sm text-muted-foreground">{role === 'event' ? `No events have ${name} as their country in this window.` : `No articles have ${name} as a ${role} country in this window.`}</p>}
            </div>
            <button className={ghostButtonClass} onClick={() => go({ country: '' })}>Clear selection</button>
          </div>
          <div className="flex flex-wrap gap-2 px-6">
            {articleRole && <Link className={ghostButtonClass} href={toHref('/search', queryFromState({ ...refine(emptyInvestigation(), SEARCH_FIELD[articleRole], country), after: from }))}>Search these articles</Link>}
            {role !== 'mentioned' && role !== 'source' && <Link className={ghostButtonClass} href={toHref('/events', new URLSearchParams({ country, from }))}>Events with this country</Link>}
            {articleRole && <Link className={ghostButtonClass} href={compareHref({ kind: 'country', a: country, role: articleRole, days })}>Compare with another country</Link>}
          </div>
          {found && articleRole && (
            <>
              {articles.isPending && <Note>Loading articles…</Note>}
              {articles.isError && <Note error>Could not load articles.</Note>}
              {articleItems.map(article => (
                <div key={article.id} className="flex flex-col gap-0.5 px-6 py-3">
                  <Link className="text-[15px] font-semibold text-foreground hover:underline" href={openArticle(article.id)}>{article.title}</Link>
                  <span className="text-xs text-muted-foreground">{when(article.published_at ?? article.first_discovered_at)}</span>
                </div>
              ))}
              {articles.hasNextPage && <div className="px-6 pb-4"><button className={ghostButtonClass} disabled={articles.isFetchingNextPage} onClick={() => articles.fetchNextPage()}>{articles.isFetchingNextPage ? 'Loading…' : 'Load more articles'}</button></div>}
            </>
          )}
          {found && !articleRole && (
            <>
              {events.isPending && <Note>Loading events…</Note>}
              {events.isError && <Note error>Could not load events.</Note>}
              {eventItems.map(event => (
                <div key={event.id} className="flex flex-col gap-0.5 px-6 py-3">
                  <Link className="text-[15px] font-semibold text-foreground hover:underline" href={eventHref(event.id, currentHref)}>{event.headline ?? 'Event'}</Link>
                  <span className="text-xs text-muted-foreground">{`${when(event.started_at)} – ${when(event.ended_at)} · ${plural(event.article_count, 'article')}`}</span>
                </div>
              ))}
              {events.hasNextPage && <div className="px-6 pb-4"><button className={ghostButtonClass} disabled={events.isFetchingNextPage} onClick={() => events.fetchNextPage()}>{events.isFetchingNextPage ? 'Loading…' : 'Load more events'}</button></div>}
            </>
          )}
        </GlassPanel>
      )}
    </div>
  )
}

export default function MapPage() {
  return <Suspense fallback={null}><MapContent /></Suspense>
}
