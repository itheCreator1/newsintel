'use client'

import { useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { Suspense } from 'react'
import { api } from '../../lib/api'
import type { OpsArea, OpsFeed, OpsJobPipeline, OpsPipelines } from '../../lib/api-types'
import { GlassPanel } from '../../components/GlassPanel'
import { PageHeader } from '../../components/PageHeader'
import { StatusBadge } from '../../components/StatusBadge'
import { sourceHref } from '../../lib/investigation'
import { AREAS, DEFAULT_HOURS, FEED_FILTERS, HOURS, bytes, feedTone, operationsHref, probeTone, since, span, type FeedFilter } from '../../lib/operations'
import { fieldClass, ghostButtonClass, labelClass } from '../../lib/ui-classes'
import { plural } from '../../lib/utils'

const PROBES: Record<string, string> = { postgres: 'PostgreSQL', redis: 'Redis', elasticsearch: 'Elasticsearch', nlp: 'NLP processors', scheduler: 'Scheduler', workers: 'Workers' }
const PROBE_STATES = { ok: 'OK', degraded: 'Degraded', down: 'Down', unknown: 'Unknown' } as const
const FEED_STATES = { ok: 'OK', overdue: 'Overdue', failing: 'Failing', awaiting: 'Awaiting first fetch', disabled: 'Disabled' } as const
const JOBS_LINK = new Set(['article', 'search', 'nlp'])
const JOB_AREA: Record<string, OpsArea> = { article: 'article', search: 'search', nlp: 'nlp', clustering: 'cluster' }
const when = (value: string | null | undefined) => value ? new Date(value).toLocaleString() : '—'
const hoursText = (hours: number) => hours === 1 ? '1 hour' : `${hours} hours`

function Note({ children, error }: { children: string; error?: boolean }) {
  return <p className={error ? 'error px-6 py-4 text-sm text-destructive' : 'px-6 py-4 text-sm text-muted-foreground'}>{children}</p>
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return <div className="flex flex-col gap-0.5"><dt className="text-xs text-muted-foreground">{label}</dt><dd className="m-0 font-mono text-sm text-foreground">{value}</dd></div>
}

function AsOf({ at }: { at: string }) {
  return <span className="text-xs text-muted-foreground">As of {when(at)}</span>
}

function PipelineRow({ job, onFailures }: { job: OpsJobPipeline; onFailures: () => void }) {
  const num = 'px-3 py-2 align-top font-mono text-foreground'
  return (
    <tr className="border-t border-border">
      <th scope="row" className="px-4 py-2 text-left align-top font-normal">
        <span className="block text-foreground">{job.label}</span>
        <span className="block text-xs text-muted-foreground">{job.definition}</span>
        <span className="block text-xs text-muted-foreground">Window counts {job.window_basis}.</span>
      </th>
      <td className={num}>{job.queued}</td>
      <td className={num}>{job.running}</td>
      <td className={num}>{job.retrying}</td>
      <td className={num}>{job.failed}</td>
      <td className={num}>{job.lease_expired}</td>
      <td className={num}>{span(job.oldest_wait_seconds)}</td>
      <td className={num}>{job.completed_in_window}</td>
      <td className={num}>{job.failed_in_window}</td>
      <td className="flex flex-wrap gap-2 px-3 py-2 align-top">
        <button className={ghostButtonClass} onClick={onFailures}>Failures</button>
        {JOBS_LINK.has(job.key) && <Link className={ghostButtonClass} href="/jobs/">Jobs</Link>}
      </td>
    </tr>
  )
}

function Pipelines({ data, onFailures }: { data: OpsPipelines; onFailures: (area: OpsArea) => void }) {
  const { events, monitors } = data
  const heads = ['Queued', 'Running', 'Retrying', 'Failed', 'Lease expired', 'Oldest wait', 'Done in window', 'Failed in window']
  return (
    <>
      <GlassPanel aria-label="Pipelines" className="flex flex-col gap-3 p-0">
        <div className="flex flex-wrap items-baseline justify-between gap-2 px-6 pt-6">
          <h3 className="text-lg font-semibold text-foreground">Pipelines</h3>
          <AsOf at={data.generated_at} />
        </div>
        <p className="px-6 text-xs text-muted-foreground">
          Queued, running, retrying and failed are the current state. Oldest wait is the age of the oldest queued or retrying item that is due. A lease expired means a worker took an item and stopped renewing it, so it is probably dead. Workers is Down when a queue has no live worker; a live but stuck worker shows as a growing wait or lease count.
        </p>
        <div className="overflow-x-auto">
          <table aria-label="Job pipelines" className="w-full text-left text-sm">
            <thead><tr className="text-xs text-muted-foreground"><th className="px-4 py-2 font-medium">Pipeline</th>{heads.map(head => <th key={head} className="px-3 py-2 font-medium">{head}</th>)}<th className="px-3 py-2 font-medium"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{data.jobs.map(job => <PipelineRow key={job.key} job={job} onFailures={() => onFailures(JOB_AREA[job.key])} />)}</tbody>
          </table>
        </div>
      </GlassPanel>

      <div className="grid gap-6 lg:grid-cols-2">
        <GlassPanel aria-label={events.label} className="flex flex-col gap-3">
          <div className="flex items-baseline justify-between gap-2"><h3 className="text-lg font-semibold text-foreground">{events.label}</h3><button className={ghostButtonClass} onClick={() => onFailures('event')}>Failures</button></div>
          <p className="text-xs text-muted-foreground">{events.definition}</p>
          <dl className="grid grid-cols-2 gap-3">
            <Stat label="Dirty clusters" value={events.dirty_clusters} />
            <Stat label="Last run" value={events.last_run_at ? `${since(events.last_run_at, data.generated_at)} ago` : '—'} />
            <Stat label="Last run without failure" value={events.last_success_at ? `${since(events.last_success_at, data.generated_at)} ago` : '—'} />
            <Stat label="Runs in window" value={events.runs_in_window} />
            <Stat label="Failed runs" value={events.failed_runs_in_window} />
            <Stat label="Failed cluster decisions" value={events.failed_clusters_in_window} />
          </dl>
          {events.last_error && <p className="text-xs text-destructive">Last failure {when(events.last_error.at)}: {events.last_error.message ?? events.last_error.category}</p>}
        </GlassPanel>
        <GlassPanel aria-label={monitors.label} className="flex flex-col gap-3">
          <div className="flex items-baseline justify-between gap-2"><h3 className="text-lg font-semibold text-foreground">{monitors.label}</h3><button className={ghostButtonClass} onClick={() => onFailures('monitor')}>Failures</button></div>
          <p className="text-xs text-muted-foreground">{monitors.definition}</p>
          <p className="text-xs text-muted-foreground">Counts across all users; names and queries are not shown here.</p>
          <dl className="grid grid-cols-2 gap-3">
            <Stat label="Monitors" value={monitors.total} />
            <Stat label="Enabled" value={monitors.enabled} />
            <Stat label="Due now" value={monitors.due} />
            <Stat label="Oldest overdue" value={span(monitors.oldest_overdue_seconds)} />
            <Stat label="In error" value={monitors.in_error} />
          </dl>
          {monitors.by_error_category.length > 0 && <p className="text-xs text-muted-foreground">{monitors.by_error_category.map(item => `${item.category}: ${item.count}`).join(' · ')}</p>}
        </GlassPanel>
      </div>
    </>
  )
}

function FeedItem({ feed, generatedAt }: { feed: OpsFeed; generatedAt: string }) {
  const streak = feed.failure_streak
  const categories = Object.entries(feed.failures_by_category)
  return (
    <li className="flex flex-col gap-1 border-t border-border px-6 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <Link className="text-[15px] font-semibold text-foreground hover:underline" href={sourceHref(feed.id)}>{feed.name}</Link>
        <StatusBadge tone={feedTone(feed.state)}>{FEED_STATES[feed.state]}</StatusBadge>
        {feed.state === 'overdue' && <span className="text-xs text-muted-foreground">Overdue by {span(feed.overdue_seconds)}</span>}
        {streak > 0 && <span className="text-xs text-destructive">{feed.streak_capped ? `${streak}+` : streak} failed {streak === 1 ? 'fetch' : 'fetches'} in a row</span>}
      </div>
      <p className="text-xs text-muted-foreground">
        Last fetch {feed.last_fetch_at ? `${since(feed.last_fetch_at, generatedAt)} ago (${feed.last_fetch_status})` : 'never'} · last success {feed.last_success_at ? `${since(feed.last_success_at, generatedAt)} ago` : 'never'} · next poll {when(feed.next_poll_at)} · every {feed.poll_interval_minutes} min
      </p>
      {categories.length > 0 && <p className="text-xs text-muted-foreground">{categories.map(([name, count]) => `${name} ${count}`).join(' · ')}</p>}
    </li>
  )
}

function OperationsContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const hours = HOURS.includes(Number(searchParams.get('hours'))) ? Number(searchParams.get('hours')) : DEFAULT_HOURS
  const area = AREAS.find(item => item.value === searchParams.get('area'))?.value
  const filter = (FEED_FILTERS as readonly string[]).includes(searchParams.get('feeds') ?? '') ? searchParams.get('feeds') as FeedFilter : 'all'
  const go = (next: { hours?: number; area?: OpsArea | null; feeds?: FeedFilter }) => {
    const merged = { hours, area, feeds: filter, ...next }
    router.replace(operationsHref({ ...merged, area: merged.area ?? undefined }))
  }

  const health = useQuery({ queryKey: ['ops-health'], queryFn: api.opsHealth, refetchInterval: 10_000, retry: false })
  const pipelines = useQuery({ queryKey: ['ops-pipelines', hours], queryFn: () => api.opsPipelines(hours), refetchInterval: 10_000, retry: false })
  const feeds = useQuery({ queryKey: ['ops-feeds', hours], queryFn: () => api.opsFeeds(hours), refetchInterval: 30_000, retry: false })
  const storage = useQuery({ queryKey: ['ops-storage'], queryFn: api.opsStorage, refetchInterval: 60_000, retry: false })
  const failures = useQuery({ queryKey: ['ops-failures', area, hours], queryFn: () => api.opsFailures(area!, hours), enabled: Boolean(area), retry: false })

  const shown = feeds.data?.items.filter(item => filter === 'all' || item.state === filter) ?? []
  const areaLabel = AREAS.find(item => item.value === area)?.label ?? ''

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="Operations" title="Operations">
        <label className={labelClass}>Window
          <select className={fieldClass} value={hours} onChange={event => go({ hours: Number(event.target.value) })}>
            {HOURS.map(option => <option key={option} value={option}>Last {hoursText(option)}</option>)}
          </select>
        </label>
      </PageHeader>

      <GlassPanel aria-label="Dependencies" className="flex flex-col gap-3 p-0">
        <div className="flex flex-wrap items-baseline justify-between gap-2 px-6 pt-6">
          <h3 className="text-lg font-semibold text-foreground">Dependencies</h3>
          {health.data && <AsOf at={health.data.generated_at} />}
        </div>
        {health.isPending && <Note>Checking dependencies…</Note>}
        {health.isError && <Note error>Could not check dependencies.</Note>}
        {health.data && (
          <>
            <p className="px-6 text-xs text-muted-foreground">Each check has a 2 second limit; an unreachable service is shown as Down here instead of an error. Workers checks that each queue has a live worker process; whether it keeps up shows in each pipeline’s oldest wait and expired leases.</p>
            <ul className="flex flex-col">
              {health.data.probes.map(probe => (
                <li key={probe.name} className="flex flex-wrap items-center gap-3 border-t border-border px-6 py-3">
                  <span className="w-36 text-sm text-foreground">{PROBES[probe.name] ?? probe.name}</span>
                  <StatusBadge tone={probeTone(probe.state)}>{PROBE_STATES[probe.state]}</StatusBadge>
                  {probe.latency_ms !== null && <span className="font-mono text-xs text-muted-foreground">{probe.latency_ms} ms</span>}
                  {probe.detail && <span className="text-xs text-muted-foreground">{probe.detail}</span>}
                </li>
              ))}
            </ul>
            {health.data.queues.length > 0 && (
              <div className="overflow-x-auto px-6 pb-6">
                <table aria-label="Queues" className="w-full text-left text-sm">
                  <thead><tr className="text-xs text-muted-foreground"><th className="py-2 pr-4 font-medium">Queue</th><th className="py-2 pr-4 font-medium">Ready</th><th className="py-2 pr-4 font-medium">Delayed</th><th className="py-2 font-medium">Dead-lettered</th></tr></thead>
                  <tbody>{health.data.queues.map(queue => <tr key={queue.queue} className="border-t border-border"><th scope="row" className="py-1.5 pr-4 text-left font-normal text-foreground">{queue.queue}</th><td className="py-1.5 pr-4 font-mono">{queue.ready}</td><td className="py-1.5 pr-4 font-mono">{queue.delayed}</td><td className="py-1.5 font-mono">{queue.dead}</td></tr>)}</tbody>
                </table>
              </div>
            )}
          </>
        )}
      </GlassPanel>

      {pipelines.isPending && <GlassPanel className="p-0"><Note>Loading the pipelines…</Note></GlassPanel>}
      {pipelines.isError && <GlassPanel className="p-0"><Note error>Could not load the pipelines.</Note></GlassPanel>}
      {pipelines.data && <Pipelines data={pipelines.data} onFailures={next => go({ area: next })} />}

      <GlassPanel aria-label="Feeds" className="flex flex-col gap-3 p-0">
        <div className="flex flex-wrap items-end justify-between gap-3 px-6 pt-6">
          <h3 className="text-lg font-semibold text-foreground">Feeds</h3>
          <div className="flex flex-wrap items-end gap-3">
            {feeds.data && <AsOf at={feeds.data.generated_at} />}
            <button className={ghostButtonClass} onClick={() => go({ area: 'feed' })}>Failures</button>
            <label className={labelClass}>Show feeds
              <select className={fieldClass} value={filter} onChange={event => go({ feeds: event.target.value as FeedFilter })}>
                {FEED_FILTERS.map(option => <option key={option} value={option}>{option === 'all' ? 'All feeds' : FEED_STATES[option]}</option>)}
              </select>
            </label>
          </div>
        </div>
        <p className="px-6 text-xs text-muted-foreground">Failing: the newest finished fetches failed (counted over the last 20). Overdue: the next poll is more than one interval late. Awaiting: no fetch has finished yet.</p>
        {feeds.isPending && <Note>Loading the feeds…</Note>}
        {feeds.isError && <Note error>Could not load the feeds.</Note>}
        {feeds.data && (
          <>
            <p className="px-6 text-sm text-foreground">
              In the last {hoursText(hours)}, {plural(feeds.data.totals.fetches, 'finished fetch', 'finished fetches')} returned {plural(feeds.data.totals.entries, 'entry', 'entries')}: {feeds.data.totals.invalid} invalid, {feeds.data.totals.new} new, {plural(feeds.data.totals.duplicates, 'duplicate')} (entries − invalid − new: already stored).
            </p>
            {feeds.data.truncated && <p className="px-6 text-xs text-muted-foreground">Showing the first 500 feeds.</p>}
            {feeds.data.items.length === 0 && <Note>No feeds yet.</Note>}
            {feeds.data.items.length > 0 && shown.length === 0 && <Note>{`No feeds are ${filter === 'all' ? 'listed' : FEED_STATES[filter].toLowerCase()}.`}</Note>}
            <ul>{shown.map(item => <FeedItem key={item.id} feed={item} generatedAt={feeds.data.generated_at} />)}</ul>
          </>
        )}
      </GlassPanel>

      <GlassPanel aria-label="Storage" className="flex flex-col gap-3 p-0">
        <div className="flex flex-wrap items-baseline justify-between gap-2 px-6 pt-6">
          <h3 className="text-lg font-semibold text-foreground">Storage</h3>
          {storage.data && <AsOf at={storage.data.generated_at} />}
        </div>
        {storage.isPending && <Note>Loading storage…</Note>}
        {storage.isError && <Note error>Could not load storage.</Note>}
        {storage.data && (
          <>
            <p className="px-6 text-sm text-foreground">Database {bytes(storage.data.database_bytes)}</p>
            <p className="px-6 text-sm text-foreground">
              {storage.data.elasticsearch
                ? `Elasticsearch ${storage.data.elasticsearch.index}: ${plural(storage.data.elasticsearch.documents, 'document')}, ${bytes(storage.data.elasticsearch.store_bytes)}`
                : `Elasticsearch could not be measured (${storage.data.elasticsearch_error ?? 'no index'}).`}
            </p>
            <p className="px-6 text-sm text-foreground">{plural(storage.data.retained_html_objects, 'retained HTML object')} recorded</p>
            <p className="px-6 text-xs text-muted-foreground">{storage.data.article_files_note}</p>
            <div className="overflow-x-auto px-6 pb-6">
              <table aria-label="Tables" className="w-full text-left text-sm">
                <thead><tr className="text-xs text-muted-foreground"><th className="py-2 pr-4 font-medium">Table</th><th className="py-2 pr-4 font-medium">Size with indexes</th><th className="py-2 font-medium">Rows (planner estimate)</th></tr></thead>
                <tbody>{storage.data.tables.map(table => <tr key={table.name} className="border-t border-border"><td className="py-1.5 pr-4 text-foreground">{table.name}</td><td className="py-1.5 pr-4 font-mono">{bytes(table.total_bytes)}</td><td className="py-1.5 font-mono">≈ {table.approximate_rows}</td></tr>)}</tbody>
              </table>
            </div>
          </>
        )}
      </GlassPanel>

      {area && (
        <GlassPanel aria-label={`Failures: ${areaLabel}`} className="flex flex-col gap-3 p-0">
          <div className="flex flex-wrap items-baseline justify-between gap-2 px-6 pt-6">
            <h3 className="text-lg font-semibold text-foreground">Failures: {areaLabel}</h3>
            <button className={ghostButtonClass} onClick={() => go({ area: null })}>Close</button>
          </div>
          {failures.isPending && <Note>Loading failures…</Note>}
          {failures.isError && <Note error>Could not load the failures.</Note>}
          {failures.data && failures.data.by_category.length === 0 && failures.data.recent.length === 0 && <Note>{`No failures in the last ${hoursText(hours)}.`}</Note>}
          {failures.data && failures.data.by_category.length > 0 && (
            <ul aria-label="Failure categories" className="flex flex-wrap gap-3 px-6">
              {failures.data.by_category.map(item => <li key={item.category} className="rounded-xl border border-border px-3 py-1.5 text-sm text-muted-foreground">{item.category}: <strong className="font-mono text-foreground">{item.count}</strong></li>)}
            </ul>
          )}
          {failures.data && failures.data.recent.length > 0 && (
            <ul className="pb-4">
              {failures.data.recent.map(item => (
                <li key={item.id} className="flex flex-col gap-0.5 border-t border-border px-6 py-3">
                  <span className="text-xs text-muted-foreground">{when(item.at)}{item.status ? ` · ${item.status}` : ''}{item.error_category ? ` · ${item.error_category}` : ''}</span>
                  {item.message && <span className="text-sm text-foreground">{item.message}</span>}
                </li>
              ))}
            </ul>
          )}
        </GlassPanel>
      )}
    </div>
  )
}

export default function OperationsPage() {
  return <Suspense fallback={null}><OperationsContent /></Suspense>
}
