'use client'

import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useState, type ReactNode } from 'react'
import { BarChart, type BarChartItem } from '../components/BarChart'
import { EmptyState, ErrorNotice, LoadingState } from '../components/Feedback'
import { GlassPanel } from '../components/GlassPanel'
import { PageHeader } from '../components/PageHeader'
import { api, ApiError } from '../lib/api'
import { errorCode, isTransient } from '../lib/filter-ui'
import { emptyInvestigation, queryFromState, refine, toHref } from '../lib/investigation'
import { chipClass, fieldClass, labelClass } from '../lib/ui-classes'

const ENTITY_TYPES = ['PERSON', 'ORG', 'GPE', 'COUNTRY', 'LOCATION', 'EVENT', 'PRODUCT', 'OTHER'] as const

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
function PanelBody({ query, loading, error, empty, hasItems, children }: { query: UseQueryResult; loading: string; error: string; empty: ReactNode; hasItems: boolean; children: ReactNode }) {
  if (query.isPending) return <LoadingState label={loading} variant="chart" />
  return (
    <>
      {query.isError && <ErrorNotice message={panelError(query.error, error)} onRetry={isTransient(query.error) ? () => void query.refetch() : undefined} retrying={query.isFetching} />}
      {hasItems ? children : !query.isError && empty}
    </>
  )
}

export default function OverviewPage() {
  const router = useRouter()
  const [entityType, setEntityType] = useState('')
  const status = useQuery({ queryKey: ['status'], queryFn: api.status })
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
    id: country.country_code, label: country.country_code, value: country.count,
  }))

  return (
    <div className="flex flex-col gap-8 font-sans">
      <PageHeader eyebrow="System overview" title="Archive operations" description="See recent collection activity and explore the archive." />

      <GlassPanel className="flex w-fit min-w-[280px] items-center gap-4 px-6 py-5">
        <span className="relative mt-0.5 flex h-2.5 w-2.5 shrink-0">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary/50" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-primary shadow-glow-sm" />
        </span>
        <div>
          <strong className="block text-sm font-semibold text-foreground">Core services</strong>
          <p className="mt-0.5 text-sm capitalize text-muted-foreground">{status.data?.status || (status.isError ? 'Unavailable' : 'Checking')}</p>
        </div>
      </GlassPanel>

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
