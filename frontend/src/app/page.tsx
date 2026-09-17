'use client'

import { useQuery } from '@tanstack/react-query'
import { useRouter } from 'next/navigation'
import { useState } from 'react'
import { BarChart, type BarChartItem } from '../components/BarChart'
import { GlassPanel } from '../components/GlassPanel'
import { PageHeader } from '../components/PageHeader'
import { api } from '../lib/api'
import { emptyInvestigation, queryFromState, refine, toHref } from '../lib/investigation'
import { fieldClass, labelClass } from '../lib/ui-classes'

const ENTITY_TYPES = ['PERSON', 'ORG', 'GPE', 'COUNTRY', 'LOCATION', 'EVENT', 'PRODUCT', 'OTHER'] as const

function nextDay(day: string): string {
  const date = new Date(`${day}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + 1)
  return date.toISOString().slice(0, 10)
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
      <PageHeader eyebrow="System overview" title="Archive operations" />

      <GlassPanel className="flex w-fit min-w-[280px] items-center gap-4 px-6 py-5">
        <span className="relative mt-0.5 flex h-2.5 w-2.5 shrink-0">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary/50" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-primary shadow-glow-sm" />
        </span>
        <div>
          <strong className="block text-sm font-semibold text-foreground">Core services</strong>
          <p className="mt-0.5 text-sm capitalize text-muted-foreground">{status.data?.status || 'Checking'}</p>
        </div>
      </GlassPanel>

      <div className="flex flex-col gap-6">
        <GlassPanel>
          <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
            <h3 className="text-sm font-semibold text-foreground">Ingestion, last 30 days</h3>
          </div>
          {ingestion.isPending && <p className="text-sm text-muted-foreground">Loading ingestion timeline…</p>}
          {!ingestion.isPending && ingestion.isError && <p className="error text-sm text-destructive" role="alert">Could not load the ingestion timeline.</p>}
          {!ingestion.isPending && !ingestion.isError && !ingestionItems.length && <p className="text-sm text-muted-foreground">No articles ingested yet.</p>}
          {!ingestion.isPending && !ingestion.isError && ingestionItems.length > 0 && (
            <BarChart
              items={ingestionItems}
              valueLabel="articles"
              ariaLabel={`Articles ingested per day over the last ${ingestionItems.length} days. Flagged bars are spikes. Click a bar to open that day's articles.`}
              onSelect={item => openSearch({ ...emptyInvestigation(), after: item.id, before: nextDay(item.id) })}
            />
          )}
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
            {entities.isPending && <p className="text-sm text-muted-foreground">Loading top entities…</p>}
            {!entities.isPending && entities.isError && <p className="error text-sm text-destructive" role="alert">Could not load top entities.</p>}
            {!entities.isPending && !entities.isError && !entityItems.length && <p className="text-sm text-muted-foreground">No entities found yet.</p>}
            {!entities.isPending && !entities.isError && entityItems.length > 0 && (
              <BarChart
                items={entityItems}
                orientation="horizontal"
                valueLabel="articles"
                ariaLabel="Top named entities over the last 30 days. Click a bar to search articles mentioning that entity."
                onSelect={item => openSearch(refine(emptyInvestigation(), 'entity_id', item.id))}
              />
            )}
          </GlassPanel>
          <GlassPanel>
            <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
              <h3 className="text-sm font-semibold text-foreground">Top countries</h3>
            </div>
            {countries.isPending && <p className="text-sm text-muted-foreground">Loading top countries…</p>}
            {!countries.isPending && countries.isError && <p className="error text-sm text-destructive" role="alert">Could not load top countries.</p>}
            {!countries.isPending && !countries.isError && !countryItems.length && <p className="text-sm text-muted-foreground">No countries found yet.</p>}
            {!countries.isPending && !countries.isError && countryItems.length > 0 && (
              <BarChart
                items={countryItems}
                orientation="horizontal"
                valueLabel="articles"
                ariaLabel="Top primary story countries over the last 30 days. Click a bar to search articles from that country."
                onSelect={item => openSearch(refine(emptyInvestigation(), 'story_country', item.id))}
              />
            )}
          </GlassPanel>
        </div>
      </div>
    </div>
  )
}
