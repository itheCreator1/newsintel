'use client'

import { useQuery } from '@tanstack/react-query'
import { useRouter } from 'next/navigation'
import { useState } from 'react'
import { BarChart, type BarChartItem } from '../components/BarChart'
import { api } from '../lib/api'
import { emptyInvestigation, queryFromState, refine, toHref } from '../lib/investigation'

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
    <>
      <header><div><p className="eyebrow">System overview</p><h2>Archive operations</h2></div></header>
      <section className="status-card">
        <span className="status-dot" />
        <div><strong>Core services</strong><p>{status.data?.status || 'Checking'}</p></div>
      </section>

      <div className="overview-panels">
        <section className="panel timeline">
          <div className="panel-heading"><h3>Ingestion, last 30 days</h3></div>
          {ingestion.error && <p className="error" role="alert">Could not load the ingestion timeline.</p>}
          {ingestion.data && (
            <BarChart
              items={ingestionItems}
              valueLabel="articles"
              ariaLabel={`Articles ingested per day over the last ${ingestionItems.length} days. Flagged bars are spikes. Click a bar to open that day's articles.`}
              onSelect={item => openSearch({ ...emptyInvestigation(), after: item.id, before: nextDay(item.id) })}
            />
          )}
        </section>

        <section className="panel two-column">
          <div>
            <div className="panel-heading">
              <h3>Top entities</h3>
              <label>Entity type
                <select value={entityType} onChange={event => setEntityType(event.target.value)}>
                  <option value="">All types</option>
                  {ENTITY_TYPES.map(kind => <option key={kind}>{kind}</option>)}
                </select>
              </label>
            </div>
            {entities.error && <p className="error" role="alert">Could not load top entities.</p>}
            {entities.data && (
              <BarChart
                items={entityItems}
                orientation="horizontal"
                valueLabel="articles"
                ariaLabel="Top named entities over the last 30 days. Click a bar to search articles mentioning that entity."
                onSelect={item => openSearch(refine(emptyInvestigation(), 'entity_id', item.id))}
              />
            )}
          </div>
          <div>
            <div className="panel-heading"><h3>Top countries</h3></div>
            {countries.error && <p className="error" role="alert">Could not load top countries.</p>}
            {countries.data && (
              <BarChart
                items={countryItems}
                orientation="horizontal"
                valueLabel="articles"
                ariaLabel="Top primary story countries over the last 30 days. Click a bar to search articles from that country."
                onSelect={item => openSearch(refine(emptyInvestigation(), 'story_country', item.id))}
              />
            )}
          </div>
        </section>
      </div>
    </>
  )
}
