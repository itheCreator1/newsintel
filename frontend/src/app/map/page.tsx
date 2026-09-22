'use client'

import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense } from 'react'
import { api } from '../../lib/api'
import type { GeoArticleRole, GeoCountriesResponse, GeoRole } from '../../lib/api-types'
import { ActiveFilterBar } from '../../components/ActiveFilterBar'
import { GeoChart } from '../../components/GeoChart'
import { GlassPanel } from '../../components/GlassPanel'
import { PageHeader } from '../../components/PageHeader'
import { WatchForm } from '../../components/WatchForm'
import world from '../../lib/world.geo.json'
import { errorCode, filterChips, removeFilter, SEARCH_CHIP_FIELDS } from '../../lib/filter-ui'
import { compareHref, emptyInvestigation, eventHref, mapHref, mapStateFromQuery, queryFromState, refine, searchParams as criteriaOf, toHref, type Investigation, type ListField } from '../../lib/investigation'
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
const DRAWN = new Set(world.features.map(feature => feature.properties.code))
const SEARCH_FIELD: Record<GeoArticleRole, ListField> = { story: 'story_country', mentioned: 'mentioned_country', source: 'source_country' }
const when = (value: string | null | undefined) => value ? new Date(value).toLocaleString() : '—'
const pageOf = <T extends { next_cursor: string | null }>(load: (cursor?: string) => Promise<T>) => ({
  initialPageParam: undefined as string | undefined,
  queryFn: ({ pageParam }: { pageParam: string | undefined }) => load(pageParam),
  getNextPageParam: (page: T) => page.next_cursor ?? undefined,
  retry: false,
})
// A map applies every criterion; sort orders a result list and means nothing here.
const mapCriteria = (state: Investigation) => Object.fromEntries(Object.entries(criteriaOf(state)).filter(([key]) => key !== 'sort'))

function Note({ children, error }: { children: string; error?: boolean }) {
  return <p className={error ? 'error px-6 py-4 text-sm text-destructive' : 'px-6 py-4 text-sm text-muted-foreground'}>{children}</p>
}

/** An investigation's story and source counts are cardinality estimates: `≈` on screen, "estimated" to a screen reader. */
function Count({ value, estimated }: { value: number | null; estimated: boolean }) {
  if (value === null) return null
  return estimated ? <>≈{value}<span className="sr-only"> (estimated)</span></> : <>{value}</>
}

const about = (count: number, one: string, many: string, estimated: boolean) => estimated ? `about ${plural(count, one, many)} (estimated)` : plural(count, one, many)

function coverageText(data: GeoCountriesResponse, role: GeoRole, days: number) {
  const { unit, window_total: total, located } = data.coverage
  const kind = unit === 'events' ? 'a country' : `a ${role} country`
  if (data.scope === 'investigation') {
    const from = data.window_start?.slice(0, 10), until = data.window_end?.slice(0, 10)
    const base = `matching articles${from ? ` published from ${from}` : ''}${until ? ` before ${until}` : ''}`
    if (!total) return `No ${base}.`
    if (!located) return `None of the ${total} ${base} has ${kind}.`
    return `${located} of ${total} ${base} have ${kind}; ${total - located} have none.`
  }
  const noun = unit === 'events' ? 'events' : 'articles'
  if (!total) return `No ${noun} in the last ${days} days.`
  if (!located) return `None of the ${total} ${noun} in the last ${days} days has ${kind}.`
  return `${located} of ${total} ${noun} in the last ${days} days have ${kind}; ${total - located} have none.`
}

function MapContent() {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const currentHref = toHref(pathname, searchParams)
  const view = mapStateFromQuery(searchParams)
  const investigation = view.investigation ? view.state : undefined
  const requested = ROLES.find(item => item.value === searchParams.get('role'))?.value ?? 'story'
  // Events carry no article criteria, so an investigation falls back to the story role.
  const role: GeoRole = investigation && requested === 'event' ? 'story' : requested
  const days = WINDOWS.includes(Number(searchParams.get('days'))) ? Number(searchParams.get('days')) : 30
  const country = view.selected
  const definition = ROLES.find(item => item.value === role)!
  const scope: Record<string, string | string[]> = investigation ? { scope: 'investigation', ...mapCriteria(investigation) } : { days: String(days) }

  const map = useQuery({ queryKey: ['geo-countries', role, scope], queryFn: () => api.geoCountries(role, scope), retry: false })
  const data = map.data
  const found = data?.items.find(item => item.country_code === country)
  const start = data?.window_start ?? ''
  const from = start.slice(0, 10)
  const articleRole = role === 'event' ? undefined : role
  const articles = useInfiniteQuery({ queryKey: ['geo-articles', role, country, scope], ...pageOf(cursor => api.geoArticles(articleRole!, country, scope, cursor)), enabled: Boolean(found && articleRole) })
  const events = useInfiniteQuery({ queryKey: ['geo-events', country, start], ...pageOf(cursor => api.events({ country, from: start }, cursor)), enabled: Boolean(found && !articleRole) })

  const value = (item: GeoCountriesResponse['items'][number]) => item.events ?? item.articles ?? 0
  const unit = role === 'event' ? 'events' : 'articles'
  const go = (next: { role?: GeoRole; days?: number; country?: string; investigation?: Investigation }) => router.replace(mapHref({ role, days, country, investigation, ...next }))
  const toggle = (code: string) => go({ country: code === country ? '' : code })
  const name = country ? countryName(country) : ''
  const openArticle = (id: string) => toHref('/articles', new URLSearchParams({ article: id, from: currentHref }))
  const articleItems = articles.data?.pages.flatMap(page => page.items) ?? []
  const skipped = articles.data?.pages.reduce((total, page) => total + page.skipped_stale, 0) ?? 0
  const eventItems = events.data?.pages.flatMap(page => page.items) ?? []
  const stories = data?.stories_estimated ?? false
  const feeds = data?.sources_estimated ?? false
  // The selected country's articles, as an ordinary search: the investigation narrowed to it, or a recent window.
  const searchCountry = (field: ListField) => toHref('/search', queryFromState(investigation ? refine(investigation, field, country) : { ...refine(emptyInvestigation(), field, country), after: from }))

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="Map" title="Map">
        <label className={labelClass}>Location role
          <select className={fieldClass} value={role} onChange={event => go({ role: event.target.value as GeoRole })}>
            {ROLES.map(item => <option key={item.value} value={item.value} disabled={Boolean(investigation) && item.value === 'event'}>{item.label}</option>)}
          </select>
        </label>
        {!investigation && (
          <label className={labelClass}>Window
            <select className={fieldClass} value={days} onChange={event => go({ days: Number(event.target.value) })}>
              {WINDOWS.map(option => <option key={option} value={option}>Last {option} days</option>)}
            </select>
          </label>
        )}
      </PageHeader>

      {investigation && (
        <GlassPanel aria-label="Investigation" className="flex flex-col gap-3">
          <p className="text-sm text-foreground">Mapping an investigation: every matching article, with no rolling window. Articles are exact; stories and sources are estimates.</p>
          <ActiveFilterBar items={filterChips(investigation, SEARCH_CHIP_FIELDS, {}, { content: true })} onRemove={key => go({ investigation: removeFilter(investigation, key) })} onClear={() => go({ investigation: emptyInvestigation() })} />
          <p className="text-xs text-muted-foreground">Event country maps events, which have no article criteria. Use a recent window to map events.</p>
          <div className="flex flex-wrap gap-2">
            <Link className={ghostButtonClass} href={toHref('/search', queryFromState(investigation))}>Back to Search</Link>
            <button type="button" className={ghostButtonClass} onClick={() => go({ investigation: undefined })}>Use a recent window</button>
          </div>
        </GlassPanel>
      )}

      <GlassPanel className="flex flex-col gap-2">
        <p className="text-sm text-foreground">{definition.note}</p>
        <p className="text-xs text-muted-foreground">An article with several countries counts once under each. Roles are never added together, so switch roles to compare them.</p>
        {data && <p className="text-sm text-foreground">{coverageText(data, role, days)}</p>}
      </GlassPanel>

      {map.isPending && <GlassPanel className="p-0"><Note>Loading the map…</Note></GlassPanel>}
      {map.isError && <GlassPanel className="p-0"><Note error>{errorCode(map.error) === 'search_upgrade_required' ? 'Search upgrade required. Rebuild the search index to map an investigation.' : 'Could not load the map.'}</Note></GlassPanel>}

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
                    {role === 'event' ? <td className="py-1.5 pr-4 text-foreground">{item.events}</td> : <><td className="py-1.5 pr-4 text-foreground">{item.articles}</td><td className="py-1.5 pr-4 text-foreground"><Count value={item.stories} estimated={stories} /></td></>}
                    {role === 'source' && <td className="py-1.5 pr-4 text-foreground"><Count value={item.sources} estimated={feeds} /></td>}
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
                ? <p className="text-sm text-muted-foreground">{role === 'event' ? plural(found.events ?? 0, 'event') : `${plural(found.articles ?? 0, 'article')} · ${about(found.stories ?? 0, 'story', 'stories', stories)}${role === 'source' ? ` · ${about(found.sources ?? 0, 'source', 'sources', feeds)}` : ''}`}</p>
                : <p className="text-sm text-muted-foreground">{role === 'event' ? `No events have ${name} as their country in this window.` : `No articles have ${name} as a ${role} country in this ${investigation ? 'investigation' : 'window'}.`}</p>}
            </div>
            <button className={ghostButtonClass} onClick={() => go({ country: '' })}>Clear selection</button>
          </div>
          <div className="flex flex-wrap gap-2 px-6">
            {articleRole && <Link className={ghostButtonClass} href={searchCountry(SEARCH_FIELD[articleRole])}>Search these articles</Link>}
            {!investigation && role !== 'mentioned' && role !== 'source' && <Link className={ghostButtonClass} href={toHref('/events', new URLSearchParams({ country, from }))}>Events with this country</Link>}
            {!investigation && articleRole && <Link className={ghostButtonClass} href={compareHref({ kind: 'country', a: country, role: articleRole, days })}>Compare with another country</Link>}
          </div>
          {articleRole && <WatchForm key={`${role}-${country}`} className="px-6" kind="country" state={refine(investigation ?? emptyInvestigation(), SEARCH_FIELD[articleRole], country)} defaultName={`${name} (${role} country)`} label="Watch country" />}
          {found && articleRole && (
            <>
              {articles.isPending && <Note>Loading articles…</Note>}
              {articles.isError && <Note error>{errorCode(articles.error) === 'restart_search' ? 'The article list expired. Select the country again to start over.' : 'Could not load articles.'}</Note>}
              {articleItems.map(article => (
                <div key={article.id} className="flex flex-col gap-0.5 px-6 py-3">
                  <Link className="text-[15px] font-semibold text-foreground hover:underline" href={openArticle(article.id)}>{article.title}</Link>
                  <span className="text-xs text-muted-foreground">{when(article.published_at ?? article.first_discovered_at)}</span>
                </div>
              ))}
              {skipped > 0 && <Note>{`${skipped} matching ${skipped === 1 ? 'article is' : 'articles are'} no longer available and not listed.`}</Note>}
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
