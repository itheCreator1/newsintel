'use client'

import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useState, type ReactNode } from 'react'
import { BarChart, type BarChartItem } from '../components/BarChart'
import { EmptyState, ErrorNotice, LoadingState } from '../components/Feedback'
import { EntityExtractionNotice, GettingStarted } from '../components/GettingStarted'
import { GlassPanel } from '../components/GlassPanel'
import { PageHeader } from '../components/PageHeader'
import { api, ApiError } from '../lib/api'
import type { Monitor, OpsHealth, ProcessesResponse } from '../lib/api-types'
import { errorCode, isTransient } from '../lib/filter-ui'
import { emptyInvestigation, queryFromState, refine, toHref } from '../lib/investigation'
import { chipClass, fieldClass, labelClass } from '../lib/ui-classes'
import { countryLabel } from '../lib/countries'
import { countsLabel, monitorKeys, refetchEvery } from '../lib/monitors'
import { plural } from '../lib/utils'

const ENTITY_TYPES = ['PERSON', 'ORG', 'GPE', 'COUNTRY', 'LOCATION', 'EVENT', 'PRODUCT', 'TOPIC', 'OTHER'] as const

function nextDay(day: string): string {
  const date = new Date(`${day}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + 1)
  return date.toISOString().slice(0, 10)
}

/** Upgrade and outage belong to search as a whole, so say so instead of blaming the panel. */
function panelError(error: unknown, fallback: string): string {
  if (errorCode(error) === 'search_upgrade_required') return 'Search upgrade required. Rebuild the search index to see analytics.'
  if (error instanceof ApiError && error.status === 503) return 'Analytics are temporarily unavailable.'
  return fallback
}

/** Skeleton only before the first answer; a failed refetch keeps the chart it already has and adds a notice. */
function PanelBody({ query, loading, error, empty, hasItems, children, variant = 'chart', analytics = true }: { query: UseQueryResult; loading: string; error: string; empty: ReactNode; hasItems: boolean; children: ReactNode; variant?: 'chart' | 'rows'; analytics?: boolean }) {
  if (query.isPending) return <LoadingState label={loading} variant={variant} />
  return (
    <>
      {query.isError && <ErrorNotice message={analytics ? panelError(query.error, error) : error} onRetry={isTransient(query.error) ? () => void query.refetch() : undefined} retrying={query.isFetching} />}
      {hasItems ? children : !query.isError && empty}
    </>
  )
}

const DAY_MS = 24 * 60 * 60 * 1000
const PROBE_NAMES: Record<string, string> = { postgres: 'The database', redis: 'Redis', elasticsearch: 'Elasticsearch', scheduler: 'The scheduler', workers: 'The workers' }
const PROCESS_TROUBLE = new Set(['failing', 'stalled'])
const average = (value: number) => value.toLocaleString('en', { maximumFractionDigits: 1 })

/** One sentence per service or process that needs attention; nothing when all is well or still unknown. */
function healthProblems(health: OpsHealth | undefined, processes: ProcessesResponse | undefined): string[] {
  const services = (health?.probes ?? []).filter(probe => probe.state === 'down' || probe.state === 'degraded').map(probe => {
    const name = PROBE_NAMES[probe.name] ?? probe.name
    return `${name} ${name === 'The workers' ? 'are' : 'is'} ${probe.state}.`
  })
  const stuck = (processes?.processes ?? []).filter(card => PROCESS_TROUBLE.has(card.state)).map(card => `${card.label} is ${card.state}.`)
  return [...services, ...stuck]
}

function HealthLine() {
  const health = useQuery({ queryKey: ['operations', 'health'], queryFn: api.opsHealth, retry: false, refetchInterval: 60_000 })
  const processes = useQuery({ queryKey: ['processes', 24], queryFn: () => api.processes(24), retry: false, refetchInterval: 60_000 })
  const problems = healthProblems(health.data, processes.data)
  if (!problems.length) return null
  return (
    <p role="status" aria-label="System health" className="error flex flex-wrap items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
      {problems.join(' ')}
      <Link className="underline" href="/processes/">Open Processes</Link>
    </p>
  )
}

function WatchedPanel() {
  const monitors = useQuery({ queryKey: monitorKeys.badge, queryFn: () => api.monitors('activity', undefined, 100), retry: false, refetchInterval: query => refetchEvery(query.state.data?.items) })
  // Paused investigations keep old counts, so they do not call for attention.
  const news: Monitor[] = (monitors.data?.items ?? []).filter(item => item.enabled && item.unseen_article_count > 0)
  return (
    <GlassPanel aria-label="Watched investigations" className="flex flex-col gap-3">
      <h3 className="text-sm font-semibold text-foreground">Watched investigations</h3>
      <PanelBody query={monitors} variant="rows" analytics={false} loading="Loading watched investigations…" error="Could not load your investigations." hasItems={news.length > 0}
        empty={<EmptyState title="Nothing new in your watched investigations." action={<Link className={chipClass} href="/investigations/">Open Investigations</Link>} />}>
        <ul className="flex list-none flex-col gap-3">
          {news.map(item => (
            <li key={item.id}>
              <Link className="text-sm font-semibold text-foreground hover:underline" href={`/monitors/?id=${item.id}`}>{item.name}</Link>
              <p className="text-xs text-muted-foreground">{countsLabel(item)}</p>
            </li>
          ))}
        </ul>
      </PanelBody>
    </GlassPanel>
  )
}

function EventsPanel() {
  // Fixed when the page opens, so a refetch asks for the same window.
  const [from] = useState(() => new Date(Date.now() - DAY_MS).toISOString())
  const events = useQuery({ queryKey: ['events', 'today', from], queryFn: () => api.events({ from, sort: 'biggest' }), retry: false })
  const items = (events.data?.items ?? []).slice(0, 8)
  return (
    <GlassPanel aria-label="Events of the last day" className="flex flex-col gap-3">
      <h3 className="text-sm font-semibold text-foreground">Events of the last day</h3>
      <PanelBody query={events} variant="rows" analytics={false} loading="Loading events…" error="Could not load events." hasItems={items.length > 0} empty={<EmptyState title="No events in the last day." />}>
        <ul className="flex list-none flex-col gap-3">
          {items.map(item => (
            <li key={item.id}>
              <Link className="text-sm font-semibold text-foreground hover:underline" href={`/events/detail/?id=${item.id}`}>{item.headline ?? 'Untitled event'}</Link>
              <p className="text-xs text-muted-foreground">{plural(item.article_count, 'article')} · {plural(item.source_count, 'source')}</p>
            </li>
          ))}
        </ul>
      </PanelBody>
    </GlassPanel>
  )
}

function RisingPanel() {
  const rising = useQuery({ queryKey: ['analytics-rising-entities'], queryFn: api.risingEntities, retry: false })
  const items = rising.data?.entities ?? []
  return (
    <GlassPanel aria-label="Rising names" className="flex flex-col gap-3">
      <h3 className="text-sm font-semibold text-foreground">Rising names</h3>
      <p className="text-xs text-muted-foreground">Named far more in the last day than on an average day of the week before.</p>
      <PanelBody query={rising} variant="rows" loading="Loading rising names…" error="Could not load rising names." hasItems={items.length > 0} empty={<EmptyState title="No name stands out today." />}>
        <ul className="flex list-none flex-col gap-3">
          {items.map(item => (
            <li key={item.entity_id}>
              <Link className="text-sm font-semibold text-foreground hover:underline" href={`/entities/?id=${item.entity_id}`}>{item.display_text}</Link>
              <p className="text-xs text-muted-foreground">{item.today} today · {average(item.daily_average)} a day before</p>
            </li>
          ))}
        </ul>
      </PanelBody>
    </GlassPanel>
  )
}

export default function TodayPage() {
  const router = useRouter()
  const [entityType, setEntityType] = useState('')
  const ingestion = useQuery({ queryKey: ['analytics-ingestion-timeline'], queryFn: api.ingestionTimeline })
  const entities = useQuery({ queryKey: ['analytics-top-entities', entityType], queryFn: () => api.topEntities(entityType || undefined) })
  const countries = useQuery({ queryKey: ['analytics-top-countries'], queryFn: api.topCountries })

  function openSearch(state: ReturnType<typeof emptyInvestigation>) {
    router.push(toHref('/search', queryFromState(state)))
  }

  const ingestionItems: BarChartItem[] = (ingestion.data?.buckets ?? []).map(bucket => ({
    id: bucket.date, label: bucket.date.slice(5), value: bucket.count, highlight: bucket.is_spike,
  }))
  const entityItems: BarChartItem[] = (entities.data?.entities ?? []).map(entity => ({
    id: entity.entity_id, label: entity.display_text, value: entity.count,
  }))
  const countryItems: BarChartItem[] = (countries.data?.countries ?? []).map(country => ({
    id: country.country_code, label: countryLabel(country.country_code), value: country.count,
  }))

  return (
    <div className="flex flex-col gap-8 font-sans">
      <PageHeader eyebrow="What is new" title="Today" description="News from your watched investigations, the day's biggest events and the names suddenly in the news." />

      <HealthLine />
      <GettingStarted />
      <EntityExtractionNotice />

      <div className="grid gap-6 lg:grid-cols-3">
        <WatchedPanel />
        <EventsPanel />
        <RisingPanel />
      </div>

      <div className="flex flex-col gap-6">
        <GlassPanel>
          <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
            <h3 className="text-sm font-semibold text-foreground">Ingestion, last 30 days</h3>
          </div>
          <PanelBody
            query={ingestion} loading="Loading ingestion timeline…" error="Could not load the ingestion timeline." hasItems={ingestionItems.length > 0}
            empty={<EmptyState title="No articles ingested yet." description="Articles appear here once a source has been collected." action={<Link className={chipClass} href="/sources/">Manage sources</Link>} />}
          >
            <BarChart
              items={ingestionItems}
              valueLabel="articles"
              ariaLabel={`Articles ingested per day over the last ${ingestionItems.length} days. Flagged bars are spikes. Click a bar to open that day's articles.`}
              onSelect={item => openSearch({ ...emptyInvestigation(), after: item.id, before: nextDay(item.id) })}
            />
          </PanelBody>
        </GlassPanel>

        <div className="grid gap-6 md:grid-cols-2">
          <GlassPanel>
            <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
              <h3 className="text-sm font-semibold text-foreground">Top entities</h3>
              <label className={labelClass}>
                Entity type
                <select value={entityType} onChange={event => setEntityType(event.target.value)} className={fieldClass}>
                  <option value="">All types</option>
                  {ENTITY_TYPES.map(kind => <option key={kind}>{kind}</option>)}
                </select>
              </label>
            </div>
            <PanelBody query={entities} loading="Loading top entities…" error="Could not load top entities." hasItems={entityItems.length > 0} empty={<EmptyState title="No entities found yet." />}>
              <BarChart
                items={entityItems}
                orientation="horizontal"
                valueLabel="articles"
                ariaLabel="Top named entities over the last 30 days. Click a bar to search articles mentioning that entity."
                onSelect={item => openSearch(refine(emptyInvestigation(), 'entity_id', item.id))}
              />
            </PanelBody>
          </GlassPanel>
          <GlassPanel>
            <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
              <h3 className="text-sm font-semibold text-foreground">Top countries</h3>
            </div>
            <PanelBody query={countries} loading="Loading top countries…" error="Could not load top countries." hasItems={countryItems.length > 0} empty={<EmptyState title="No countries found yet." />}>
              <BarChart
                items={countryItems}
                orientation="horizontal"
                valueLabel="articles"
                ariaLabel="Top primary story countries over the last 30 days. Click a bar to search articles from that country."
                onSelect={item => openSearch(refine(emptyInvestigation(), 'story_country', item.id))}
              />
            </PanelBody>
          </GlassPanel>
        </div>
      </div>
    </div>
  )
}
