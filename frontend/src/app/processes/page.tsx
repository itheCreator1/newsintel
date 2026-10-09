'use client'

import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useState } from 'react'
import { api } from '../../lib/api'
import type { ProcessKey } from '../../lib/api-types'
import { HOURS, HOUR_LABELS, LIVE_SECONDS, processesHref, readProcessesQuery, type ProcessesView } from '../../lib/processes'
import { cn } from '../../lib/utils'
import { GlassPanel } from '../../components/GlassPanel'
import { LoadError, Note } from '../../components/Feedback'
import { PageHeader } from '../../components/PageHeader'
import { ActivityList } from '../../components/processes/ActivityList'
import { FeedsPanel } from '../../components/processes/FeedsPanel'
import { ProcessCards } from '../../components/processes/ProcessCards'
import { Services } from '../../components/processes/Services'
import { StoragePanel } from '../../components/processes/StoragePanel'
import { WikidataPanel } from '../../components/processes/WikidataPanel'

const segmentClass = 'w-auto border-0 bg-transparent px-3 py-1.5 text-[13px] font-semibold text-muted-foreground aria-pressed:bg-accent aria-pressed:text-accent-foreground [&+&]:border-l [&+&]:border-border'

function ProcessesContent() {
  const router = useRouter()
  const view = readProcessesQuery(useSearchParams())
  const { hours, process, status, q, feeds: feedView } = view
  const go = (next: Partial<Omit<ProcessesView, 'process'>> & { process?: ProcessKey | null }) => {
    const merged = { ...view, ...next }
    router.replace(processesHref({ ...merged, process: merged.process ?? undefined }))
  }
  const client = useQueryClient()
  const [live, setLive] = useState(true)
  // Live refreshes stop while the tab is hidden (TanStack Query does not refetch in the background).
  const every = (ms: number) => live ? ms : false

  const processes = useQuery({ queryKey: ['processes', hours], queryFn: () => api.processes(hours), refetchInterval: every(LIVE_SECONDS * 1000), retry: false })
  const activity = useInfiniteQuery({
    queryKey: ['process-activity', hours, process, status, q], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => api.processActivity({ hours, process, status, q: q || undefined, cursor: pageParam }),
    getNextPageParam: page => page.next_cursor ?? undefined, refetchInterval: every(LIVE_SECONDS * 1000), retry: false,
  })
  const health = useQuery({ queryKey: ['ops-health'], queryFn: api.opsHealth, refetchInterval: every(10_000), retry: false })
  const nlp = useQuery({ queryKey: ['nlp-status'], queryFn: api.nlpStatus, refetchInterval: every(30_000), retry: false })
  const feeds = useQuery({ queryKey: ['ops-feeds', hours], queryFn: () => api.opsFeeds(hours), refetchInterval: every(30_000), retry: false })
  const storage = useQuery({ queryKey: ['ops-storage'], queryFn: api.opsStorage, refetchInterval: every(60_000), retry: false })
  const wikidata = useQuery({ queryKey: ['ops-wikidata'], queryFn: api.opsWikidata, refetchInterval: every(30_000), retry: false })
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ['processes'] })
    void client.invalidateQueries({ queryKey: ['process-activity'] })
  }

  const labels = Object.fromEntries((processes.data?.processes ?? []).map(card => [card.key, { label: card.label, group: card.group }]))
  const models = nlp.data?.capabilities.length ? `Models: ${nlp.data.capabilities.map(item => `${item.name} ${item.state}`).join(' · ')}` : undefined
  const windowText = HOUR_LABELS[hours]

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="System" title="Processes" description="Everything the archive does in the background: what is running, what is waiting and what failed.">
        <div className="flex flex-wrap items-center gap-2.5">
          <div role="group" aria-label="Time window" className="inline-flex overflow-hidden rounded-lg border border-input bg-card">
            {HOURS.map(option => <button key={option} type="button" className={segmentClass} aria-pressed={hours === option} onClick={() => go({ hours: option })}>{HOUR_LABELS[option]}</button>)}
          </div>
          <button
            type="button" aria-pressed={live} onClick={() => setLive(on => !on)}
            className="inline-flex w-auto items-center gap-2 rounded-full border border-border bg-transparent px-3 py-1.5 text-[13px] font-medium text-muted-foreground hover:border-ring"
          >
            <span aria-hidden="true" className={cn('size-2 rounded-full', live ? 'bg-success' : 'bg-input')} />
            {live ? `Live, every ${LIVE_SECONDS} s` : 'Paused'}
          </button>
        </div>
      </PageHeader>

      <Services health={health} />

      <GlassPanel aria-label="Processes" className="flex flex-col gap-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-lg font-semibold text-foreground">Processes</h3>
          <small className="text-xs text-muted-foreground">{`Counts are now; done and failed are in the last ${windowText}. Click a card to filter the activity.`}</small>
        </div>
        {processes.isPending && <Note>Loading the processes…</Note>}
        {processes.isError && <LoadError query={processes} message="Could not load the processes." />}
        {processes.data && (
          <ProcessCards
            data={processes.data} hours={hours} selected={process} models={models} onDone={refresh}
            onSelect={key => go(process === key ? { process: null } : { process: key, status: 'all' })}
          />
        )}
      </GlassPanel>

      <ActivityList activity={activity} labels={labels} process={process} status={status} q={q} onFilter={go} onDone={refresh} />

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,2.2fr)_minmax(0,1fr)]">
        <FeedsPanel feeds={feeds} hours={hours} view={feedView} onView={next => go({ feeds: next })} />
        <StoragePanel storage={storage} />
      </div>

      <WikidataPanel wikidata={wikidata} />
    </div>
  )
}

export default function ProcessesPage() {
  return <Suspense fallback={null}><ProcessesContent /></Suspense>
}
