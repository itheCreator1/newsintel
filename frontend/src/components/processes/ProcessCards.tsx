import { useMutation } from '@tanstack/react-query'
import { useState, type MouseEvent } from 'react'
import { api } from '../../lib/api'
import type { ProcessCard, ProcessKey, ProcessesResponse } from '../../lib/api-types'
import { since, span } from '../../lib/operations'
import { GROUPS, HOUR_LABELS, cardBadge, percent } from '../../lib/processes'
import { ghostButtonClass } from '../../lib/ui-classes'
import { cn } from '../../lib/utils'
import { StatusBadge } from '../StatusBadge'
import { count, failure } from './parts'

const COUNTS = [['Queued', 'queued'], ['Running', 'running'], ['Retrying', 'retrying'], ['Failed', 'failed']] as const
const dangerButtonClass = cn(ghostButtonClass, 'border-destructive/40 text-destructive hover:border-destructive hover:text-destructive')

function Actions({ card, onDone }: { card: ProcessCard; onDone: () => void }) {
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null)
  const done = (text: string) => { setMessage({ text }); onDone() }
  const failed = (error: unknown) => setMessage({ text: failure(error, 'That did not work.'), error: true })
  const retry = useMutation({
    mutationFn: () => api.retryProcessFailed(card.key),
    onSuccess: result => done(result.remaining ? `Retried ${count(result.retried)}; ${count(result.remaining)} still failed.` : `Retried ${count(result.retried)}.`),
    onError: failed,
  })
  const run = useMutation({
    mutationFn: () => api.runProcess(card.key),
    onSuccess: result => done(result.status === 'sent' ? 'Started.' : 'Queued; it starts within a few seconds.'),
    onError: failed,
  })
  const stop = useMutation({ mutationFn: (runId: string) => api.stopProcessRun(card.key, runId), onSuccess: () => done('Stopped.'), onError: failed })
  const actions = card.actions ?? []
  const busy = retry.isPending || run.isPending || stop.isPending
  if (!actions.length && !message) return null
  return (
    <div className="flex flex-col gap-2">
      {actions.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {actions.includes('retry_failed') && <button type="button" className={dangerButtonClass} disabled={busy} onClick={() => retry.mutate()}>{`Retry ${count(card.failed ?? 0)} failed`}</button>}
          {actions.includes('run_now') && <button type="button" className={ghostButtonClass} disabled={busy} onClick={() => run.mutate()}>Run now</button>}
          {actions.includes('stop') && card.active_run_id && (
            <button
              type="button" className={ghostButtonClass} disabled={busy}
              onClick={() => { if (window.confirm(`Stop ${card.label}? What it already did stays done.`)) stop.mutate(card.active_run_id!) }}
            >Stop</button>
          )}
        </div>
      )}
      {message && <p role="status" className={cn('text-xs', message.error ? 'error text-destructive' : 'text-muted-foreground')}>{message.text}</p>}
    </div>
  )
}

function Card({ card, generatedAt, hours, selected, models, onSelect, onDone }: {
  card: ProcessCard; generatedAt: string; hours: number; selected: boolean; models?: string; onSelect: () => void; onDone: () => void
}) {
  const badge = cardBadge(card)
  const attention = card.state === 'failing'
  const progress = card.progress
  const meta = [
    card.oldest_wait_seconds != null && `Oldest wait ${span(card.oldest_wait_seconds)}`,
    card.lease_expired ? `${count(card.lease_expired)} leases expired` : null,
    card.done_in_window != null && `Done in ${HOUR_LABELS[hours] ?? `${hours} h`} ${count(card.done_in_window)}`,
    card.last_run_at && `Last run ${since(card.last_run_at, generatedAt)} ago`,
    card.detail,
    models,
  ].filter(Boolean) as string[]
  // The whole card selects, except its own buttons; the heading button is the keyboard way in.
  const select = (event: MouseEvent) => { if (!(event.target as HTMLElement).closest('button, a')) onSelect() }
  return (
    <article
      data-attention={String(attention)}
      onClick={select}
      className={cn(
        'flex cursor-pointer flex-col gap-3 rounded-xl border border-border bg-card p-4 shadow-card transition-colors hover:border-ring',
        attention && 'border-l-[3px] border-l-destructive',
        selected && 'border-primary ring-2 ring-accent',
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="text-[15px] font-bold text-foreground">
            <button type="button" aria-pressed={selected} onClick={onSelect} className="m-0 w-auto border-0 bg-transparent p-0 text-left font-bold text-foreground hover:underline">{card.label}</button>
          </h4>
          <p className="mt-0.5 text-xs text-muted-foreground">{card.description}</p>
        </div>
        <StatusBadge tone={badge.tone}>{badge.label}</StatusBadge>
      </div>
      {card.queued != null && (
        <dl className="m-0 grid grid-cols-4 gap-1.5">
          {COUNTS.map(([label, key]) => (
            <div key={key} className="flex flex-col gap-px">
              <dt className="text-[11px] text-muted-foreground">{label}</dt>
              <dd className={cn('m-0 font-mono text-[15px] tabular-nums text-foreground', key === 'failed' && card.failed ? 'font-semibold text-destructive' : '')}>{count(card[key] ?? 0)}</dd>
            </div>
          ))}
        </dl>
      )}
      {progress && card.state === 'working' && (
        <div className="flex flex-col gap-1.5">
          {progress.total ? (
            <div role="progressbar" aria-label={`${card.label} progress`} aria-valuenow={percent(progress.done, progress.total)} aria-valuemin={0} aria-valuemax={100} className="h-1.5 overflow-hidden rounded-full bg-muted">
              <i className="block h-full rounded-full bg-primary" style={{ width: `${percent(progress.done, progress.total)}%` }} />
            </div>
          ) : null}
          <span className="font-mono text-xs text-muted-foreground">{progress.total != null ? `${count(progress.done)} of ${count(progress.total)}` : `${count(progress.done)} so far`}</span>
        </div>
      )}
      {meta.length > 0 && <p className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">{meta.map(item => <span key={item}>{item}</span>)}</p>}
      <Actions card={card} onDone={onDone} />
    </article>
  )
}

/** One card per background process, in the three groups the page is about. */
export function ProcessCards({ data, hours, selected, models, onSelect, onDone }: {
  data: ProcessesResponse; hours: number; selected?: ProcessKey; models?: string; onSelect: (key: ProcessKey) => void; onDone: () => void
}) {
  return (
    <div className="flex flex-col gap-5">
      {GROUPS.map(([group, label, hint]) => (
        <div key={group} role="group" aria-label={label} className="flex flex-col gap-2.5">
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">{label} <span className="font-medium normal-case tracking-normal">· {hint}</span></p>
          <div className={cn('grid gap-3.5 [grid-template-columns:repeat(auto-fill,minmax(250px,1fr))]', group === 'per_item' ? 'xl:grid-cols-3' : 'xl:grid-cols-4')}>
            {data.processes.filter(card => card.group === group).map(card => (
              <Card
                key={card.key} card={card} generatedAt={data.generated_at} hours={hours} selected={selected === card.key}
                models={card.key === 'nlp' ? models : undefined} onSelect={() => onSelect(card.key)} onDone={onDone}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
