'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../../lib/api'

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
    <>
      <header><div><p className="eyebrow">Article processing</p><h2>Jobs</h2></div></header>
      {backlog.isPending && <p className="muted">Loading backlog…</p>}
      {!backlog.isPending && backlog.isError && <p className="error">Could not load backlog.</p>}
      {!backlog.isPending && !backlog.isError && (
        <div className="backlog">
          <span>Queued <strong>{backlog.data?.queued || 0}</strong></span>
          <span>Running <strong>{backlog.data?.running || 0}</strong></span>
          <span>Retrying <strong>{backlog.data?.retrying || 0}</strong></span>
          <span>Failed <strong>{backlog.data?.failed || 0}</strong></span>
        </div>
      )}
      <div className="job-filters">
        <label>Stage<select value={stage} onChange={setStage}><option value="">All stages</option><option value="fetch">Fetch</option><option value="extract">Extract</option></select></label>
        <label>Status<select value={status} onChange={setStatus}><option value="">All statuses</option>{['queued', 'running', 'retrying', 'succeeded', 'failed'].map(value => <option key={value} value={value}>{value}</option>)}</select></label>
      </div>
      <section className="panel jobs">
        {jobs.isPending && <p className="muted">Loading processing jobs…</p>}
        {!jobs.isPending && jobs.isError && <p className="error">Could not load processing jobs.</p>}
        {!jobs.isPending && !jobs.isError && !jobs.data?.items.length && <p className="muted">No processing jobs.</p>}
        {retry.isPending && <p className="muted">Retrying job…</p>}
        {retry.isSuccess && <p className="success">Retry scheduled.</p>}
        {retry.isError && <p className="error">Could not retry the job.</p>}
        {jobs.data?.items.map(job => (
          <article key={job.id} className="job-row">
            <div><strong>{job.article_title}</strong><small>{job.stage} · {job.requested_mode.split('_').join(' ')}</small></div>
            <span className={`badge ${job.status === 'succeeded' ? 'healthy' : 'pending'}`}>{job.status}</span>
            {job.error_message && <p className="error">{job.error_category}: {job.error_message}</p>}
            {(job.attempts || []).length > 0 && (
              <details>
                <summary>{(job.attempts || []).length} attempts</summary>
                {(job.attempts || []).map(attempt => (
                  <p key={attempt.id} className="muted">{attempt.stage} #{attempt.attempt_number} — {attempt.status}{attempt.error_message && `: ${attempt.error_message}`}</p>
                ))}
              </details>
            )}
            {job.status === 'failed' && <button className="secondary" disabled={retry.isPending} onClick={() => retry.mutate(job.id)}>Retry</button>}
          </article>
        ))}
        {jobs.data?.next_cursor && <button className="secondary" onClick={() => setCursor(jobs.data?.next_cursor ?? undefined)}>Next page</button>}
      </section>
      <section className="panel indexing">
        <h3>Search indexing</h3>
        {indexing.isPending && <p className="muted">Loading indexing status…</p>}
        {!indexing.isPending && indexing.isError && <p className="error">Could not load indexing status.</p>}
        {!indexing.isPending && !indexing.isError && (
          <div className="backlog">
            <span>Queued <strong>{indexing.data?.queued || 0}</strong></span>
            <span>Running <strong>{indexing.data?.running || 0}</strong></span>
            <span>Retrying <strong>{indexing.data?.retrying || 0}</strong></span>
            <span>Failed <strong>{indexing.data?.failed || 0}</strong></span>
          </div>
        )}
        {indexing.data?.active_rebuild && <p className="muted">An index rebuild is active.</p>}
        {retryIndexing.isPending && <p className="muted">Scheduling indexing retry…</p>}
        {retryIndexing.isSuccess && <p className="success">Indexing retry scheduled.</p>}
        {retryIndexing.isError && <p className="error">Could not retry indexing.</p>}
        {failures.isError && <p className="error">Could not load indexing failures.</p>}
        {failures.data?.items.map(failure => (
          <article key={failure.id} className="job-row">
            <div><strong>{failure.article_id}</strong><small>{failure.index_name} · {failure.attempt_count} attempts</small></div>
            <p className="error">{failure.error_category}: {failure.error_message}</p>
            <button className="secondary" disabled={retryIndexing.isPending} onClick={() => retryIndexing.mutate(failure.article_id)}>Retry indexing</button>
          </article>
        ))}
      </section>
      <section className="panel indexing">
        <h3>NLP processing</h3>
        {nlp.isPending && <p className="muted">Loading NLP status…</p>}
        {!nlp.isPending && nlp.isError && <p className="error">Could not load NLP status.</p>}
        {!nlp.isPending && !nlp.isError && (
          <>
            <div className="backlog">
              <span>Queued <strong>{nlp.data?.queued || 0}</strong></span>
              <span>Running <strong>{nlp.data?.running || 0}</strong></span>
              <span>Retrying <strong>{nlp.data?.retrying || 0}</strong></span>
              <span>Failed <strong>{nlp.data?.failed || 0}</strong></span>
            </div>
            {nlp.data?.capabilities.map(capability => (
              <p key={capability.name} className="muted">{capability.name} · {capability.state}{capability.version && ` · ${capability.version}`}{capability.detail && ` · ${capability.detail}`}</p>
            ))}
            {nlp.data?.reprocessing.map(run => (
              <p key={String(run.id)} className="muted">Reprocessing {String(run.status)} · {Number(run.scanned) || 0} articles scanned</p>
            ))}
          </>
        )}
        {retryNlp.isPending && <p className="muted">Scheduling NLP retry…</p>}
        {retryNlp.isSuccess && <p className="success">NLP retry scheduled.</p>}
        {retryNlp.isError && <p className="error">Could not retry NLP processing.</p>}
        {nlpFailures.isError && <p className="error">Could not load NLP failures.</p>}
        {nlpFailures.data?.items.map(failure => (
          <article key={failure.id} className="job-row">
            <div><strong>{failure.article_id}</strong><small>{failure.processor} · {failure.attempt_count} attempts</small></div>
            <p className="error">{failure.error_category}: {failure.error_message}</p>
            <button className="secondary" disabled={retryNlp.isPending} onClick={() => retryNlp.mutate(failure.id)}>Retry NLP</button>
          </article>
        ))}
      </section>
    </>
  )
}
