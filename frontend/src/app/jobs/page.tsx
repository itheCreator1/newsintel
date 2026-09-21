'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { useState } from 'react'
import { api } from '../../lib/api'
import { GlassPanel } from '../../components/GlassPanel'
import { PageHeader } from '../../components/PageHeader'
import { StatusBadge, type BadgeTone } from '../../components/StatusBadge'
import { fieldClass, ghostButtonClass, labelClass } from '../../lib/ui-classes'
import { cn } from '../../lib/utils'

const JOB_TONES: Record<string, BadgeTone> = { succeeded: 'healthy', failed: 'error', retrying: 'degraded' }

function BacklogStrip({ data }: { data: { queued?: number; running?: number; retrying?: number; failed?: number } | undefined }) {
  return (
    <div className="flex flex-wrap gap-3">
      {(['queued', 'running', 'retrying', 'failed'] as const).map(key => (
        <span key={key} className="rounded-xl border border-border bg-card/40 px-4 py-2 text-sm text-muted-foreground backdrop-blur-xl">
          {key[0].toUpperCase()}{key.slice(1)} <strong className="font-mono text-foreground">{data?.[key] || 0}</strong>
        </span>
      ))}
    </div>
  )
}

export default function JobsPage() {
  const client = useQueryClient()
  const [stage, setStageValue] = useState('')
  const [status, setStatusValue] = useState('')
  const [cursor, setCursor] = useState<string | undefined>()
  const jobs = useQuery({ queryKey: ['jobs', stage, status, cursor], queryFn: () => api.jobs({ stage: stage || undefined, status: status || undefined, cursor }), refetchInterval: 5000 })
  const backlog = useQuery({ queryKey: ['jobs-backlog'], queryFn: api.backlog, refetchInterval: 5000, retry: false })
  const retry = useMutation({ mutationFn: api.retryJob, onSuccess: () => { client.invalidateQueries({ queryKey: ['jobs'] }); client.invalidateQueries({ queryKey: ['jobs-backlog'] }) } })
  const indexing = useQuery({ queryKey: ['indexing-status'], queryFn: api.indexingStatus, refetchInterval: 5000, retry: false })
  const failures = useQuery({ queryKey: ['indexing-failures'], queryFn: () => api.indexingFailures(), refetchInterval: 5000, retry: false })
  const retryIndexing = useMutation({ mutationFn: (articleId: string) => api.retryIndexing(articleId), onSuccess: () => { client.invalidateQueries({ queryKey: ['indexing-status'] }); client.invalidateQueries({ queryKey: ['indexing-failures'] }) } })
  const nlp = useQuery({ queryKey: ['nlp-status'], queryFn: api.nlpStatus, refetchInterval: 5000, retry: false })
  const nlpFailures = useQuery({ queryKey: ['nlp-failures'], queryFn: () => api.nlpFailures(), refetchInterval: 5000, retry: false })
  const retryNlp = useMutation({ mutationFn: (jobId: string) => api.retryNlpJob(jobId), onSuccess: () => { client.invalidateQueries({ queryKey: ['nlp-status'] }); client.invalidateQueries({ queryKey: ['nlp-failures'] }) } })
  function setStage(event: React.ChangeEvent<HTMLSelectElement>) { setCursor(undefined); setStageValue(event.target.value) }
  function setStatus(event: React.ChangeEvent<HTMLSelectElement>) { setCursor(undefined); setStatusValue(event.target.value) }

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="Article processing" title="Jobs">
        <Link className={ghostButtonClass} href="/operations/">Operational metrics</Link>
      </PageHeader>

      {backlog.isPending && <p className="text-sm text-muted-foreground">Loading backlog…</p>}
      {!backlog.isPending && backlog.isError && <p className="error text-sm text-destructive">Could not load backlog.</p>}
      {!backlog.isPending && !backlog.isError && <BacklogStrip data={backlog.data} />}

      <div className="flex flex-wrap gap-4">
        <label className={labelClass}>Stage<select className={cn(fieldClass, 'mt-1')} value={stage} onChange={setStage}><option value="">All stages</option><option value="fetch">Fetch</option><option value="extract">Extract</option></select></label>
        <label className={labelClass}>Status<select className={cn(fieldClass, 'mt-1')} value={status} onChange={setStatus}><option value="">All statuses</option>{['queued', 'running', 'retrying', 'succeeded', 'failed'].map(value => <option key={value} value={value}>{value}</option>)}</select></label>
      </div>

      <GlassPanel className="overflow-hidden p-0">
        {jobs.isPending && <p className="px-6 py-4 text-sm text-muted-foreground">Loading processing jobs…</p>}
        {!jobs.isPending && jobs.isError && <p className="error px-6 py-4 text-sm text-destructive">Could not load processing jobs.</p>}
        {!jobs.isPending && !jobs.isError && !jobs.data?.items.length && <p className="px-6 py-4 text-sm text-muted-foreground">No processing jobs.</p>}
        {retry.isPending && <p className="px-6 pt-4 text-sm text-muted-foreground">Retrying job…</p>}
        {retry.isSuccess && <p className="px-6 pt-4 text-sm text-primary">Retry scheduled.</p>}
        {retry.isError && <p className="error px-6 pt-4 text-sm text-destructive">Could not retry the job.</p>}
        {jobs.data?.items.map(job => (
          <article key={job.id} className="job-row flex flex-col gap-2 border-t border-border px-6 py-4 first:border-t-0">
            <div className="flex items-start justify-between gap-4">
              <div className="flex flex-col gap-0.5">
                <strong className="text-[15px] font-semibold text-foreground">{job.article_title}</strong>
                <small className="text-xs text-muted-foreground">{job.stage} · {job.requested_mode.split('_').join(' ')}</small>
              </div>
              <StatusBadge tone={JOB_TONES[job.status] ?? 'pending'}>{job.status}</StatusBadge>
            </div>
            {job.error_message && <p className="error text-sm text-destructive">{job.error_category}: {job.error_message}</p>}
            {(job.attempts || []).length > 0 && (
              <details className="text-sm text-muted-foreground">
                <summary className="cursor-pointer">{(job.attempts || []).length} attempts</summary>
                {(job.attempts || []).map(attempt => (
                  <p key={attempt.id} className="mt-1 text-sm text-muted-foreground">{attempt.stage} #{attempt.attempt_number} — {attempt.status}{attempt.error_message && `: ${attempt.error_message}`}</p>
                ))}
              </details>
            )}
            {job.status === 'failed' && <button type="button" className={cn(ghostButtonClass, 'self-start')} disabled={retry.isPending} onClick={() => retry.mutate(job.id)}>Retry</button>}
          </article>
        ))}
        {jobs.data?.next_cursor && <div className="border-t border-border px-6 py-4"><button type="button" className={ghostButtonClass} onClick={() => setCursor(jobs.data?.next_cursor ?? undefined)}>Next page</button></div>}
      </GlassPanel>

      <GlassPanel className="flex flex-col gap-3">
        <h3 className="text-sm font-semibold text-foreground">Search indexing</h3>
        {indexing.isPending && <p className="text-sm text-muted-foreground">Loading indexing status…</p>}
        {!indexing.isPending && indexing.isError && <p className="error text-sm text-destructive">Could not load indexing status.</p>}
        {!indexing.isPending && !indexing.isError && <BacklogStrip data={indexing.data} />}
        {indexing.data?.active_rebuild && <p className="text-sm text-muted-foreground">An index rebuild is active.</p>}
        {retryIndexing.isPending && <p className="text-sm text-muted-foreground">Scheduling indexing retry…</p>}
        {retryIndexing.isSuccess && <p className="text-sm text-primary">Indexing retry scheduled.</p>}
        {retryIndexing.isError && <p className="error text-sm text-destructive">Could not retry indexing.</p>}
        {failures.isError && <p className="error text-sm text-destructive">Could not load indexing failures.</p>}
        {failures.data?.items.map(failure => (
          <article key={failure.id} className="job-row flex flex-col gap-2 border-t border-border pt-3">
            <div className="flex flex-col gap-0.5">
              <strong className="text-[15px] font-semibold text-foreground">{failure.article_id}</strong>
              <small className="text-xs text-muted-foreground">{failure.index_name} · {failure.attempt_count} attempts</small>
            </div>
            <p className="error text-sm text-destructive">{failure.error_category}: {failure.error_message}</p>
            <button type="button" className={cn(ghostButtonClass, 'self-start')} disabled={retryIndexing.isPending} onClick={() => retryIndexing.mutate(failure.article_id)}>Retry indexing</button>
          </article>
        ))}
      </GlassPanel>

      <GlassPanel className="flex flex-col gap-3">
        <h3 className="text-sm font-semibold text-foreground">NLP processing</h3>
        {nlp.isPending && <p className="text-sm text-muted-foreground">Loading NLP status…</p>}
        {!nlp.isPending && nlp.isError && <p className="error text-sm text-destructive">Could not load NLP status.</p>}
        {!nlp.isPending && !nlp.isError && (
          <>
            <BacklogStrip data={nlp.data} />
            {nlp.data?.capabilities.map(capability => (
              <p key={capability.name} className="text-sm text-muted-foreground">{capability.name} · {capability.state}{capability.version && ` · ${capability.version}`}{capability.detail && ` · ${capability.detail}`}</p>
            ))}
            {nlp.data?.reprocessing.map(run => (
              <p key={String(run.id)} className="text-sm text-muted-foreground">Reprocessing {String(run.status)} · {Number(run.scanned) || 0} articles scanned</p>
            ))}
          </>
        )}
        {retryNlp.isPending && <p className="text-sm text-muted-foreground">Scheduling NLP retry…</p>}
        {retryNlp.isSuccess && <p className="text-sm text-primary">NLP retry scheduled.</p>}
        {retryNlp.isError && <p className="error text-sm text-destructive">Could not retry NLP processing.</p>}
        {nlpFailures.isError && <p className="error text-sm text-destructive">Could not load NLP failures.</p>}
        {nlpFailures.data?.items.map(failure => (
          <article key={failure.id} className="job-row flex flex-col gap-2 border-t border-border pt-3">
            <div className="flex flex-col gap-0.5">
              <strong className="text-[15px] font-semibold text-foreground">{failure.article_id}</strong>
              <small className="text-xs text-muted-foreground">{failure.processor} · {failure.attempt_count} attempts</small>
            </div>
            <p className="error text-sm text-destructive">{failure.error_category}: {failure.error_message}</p>
            <button type="button" className={cn(ghostButtonClass, 'self-start')} disabled={retryNlp.isPending} onClick={() => retryNlp.mutate(failure.id)}>Retry NLP</button>
          </article>
        ))}
      </GlassPanel>
    </div>
  )
}
