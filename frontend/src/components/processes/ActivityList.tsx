import { useMutation, type UseInfiniteQueryResult, type InfiniteData } from '@tanstack/react-query'
import Link from 'next/link'
import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import type { ActivityFilter, ActivityItem, ActivityPage, ProcessKey } from '../../lib/api-types'
import { since } from '../../lib/operations'
import { GROUPS, PROCESS_KEYS, STATUS_FILTERS, activityBadge, itemHref } from '../../lib/processes'
import { fieldClass, ghostButtonClass, labelClass } from '../../lib/ui-classes'
import { cn } from '../../lib/utils'
import { GlassPanel } from '../GlassPanel'
import { LoadError, Note } from '../Feedback'
import { StatusBadge } from '../StatusBadge'
import { count, failure } from './parts'

type Labels = Partial<Record<ProcessKey, { label: string; group: string }>>
const chipClass = 'w-auto rounded-full border border-border bg-card px-3 py-1.5 text-xs font-semibold text-muted-foreground transition-colors hover:border-ring aria-pressed:border-accent aria-pressed:bg-accent aria-pressed:text-accent-foreground'

function Row({ item, label, generatedAt, busy, onRetry, onStop }: {
  item: ActivityItem; label: string; generatedAt: string; busy: boolean; onRetry: () => void; onStop: () => void
}) {
  const href = itemHref(item.link_kind, item.link_id)
  const badge = activityBadge(item.status)
  const attempts = item.attempts ?? []
  return (
    <li className="grid grid-cols-1 gap-x-4 gap-y-1.5 border-t border-border px-6 py-3.5 first:border-t-0 sm:grid-cols-[150px_minmax(0,1fr)_auto]">
      <span className="pt-0.5 text-[13px] font-semibold text-foreground">{label}</span>
      <div className="min-w-0">
        {href
          ? <Link className="text-sm font-semibold text-foreground no-underline hover:text-primary hover:underline" href={href}>{item.title}</Link>
          : <strong className="text-sm font-semibold text-foreground">{item.title}</strong>}
        {item.detail && <small className="mt-0.5 block text-xs text-muted-foreground">{item.detail}</small>}
        {(item.error_category || item.error_message) && (
          <p className="error mt-1.5 text-[13px] text-destructive [overflow-wrap:anywhere]">
            {item.error_category && <code className="rounded bg-destructive/10 px-1.5 py-px font-mono text-xs">{item.error_category}</code>} {item.error_message}
          </p>
        )}
        {attempts.length > 1 && (
          <details className="mt-1.5 text-xs text-muted-foreground">
            <summary className="cursor-pointer">{`${attempts.length} attempts`}</summary>
            <ol className="mt-1.5 pl-5">
              {attempts.map(attempt => <li key={attempt.number}>{`Attempt ${attempt.number} · ${attempt.stage} · ${attempt.status}${attempt.error_message ? `: ${attempt.error_message}` : ''}`}</li>)}
            </ol>
          </details>
        )}
        {attempts.length === 0 && item.attempt_count != null && item.attempt_count > 1 && <small className="mt-1 block text-xs text-muted-foreground">{`${item.attempt_count} attempts`}</small>}
      </div>
      <div className="flex flex-row items-center justify-between gap-2 sm:flex-col sm:items-end">
        <StatusBadge tone={badge.tone}>{badge.label}</StatusBadge>
        <time className="whitespace-nowrap text-xs text-muted-foreground" dateTime={item.at} title={new Date(item.at).toLocaleString()}>{`${since(item.at, generatedAt)} ago`}</time>
        {item.can_retry && <button type="button" className={ghostButtonClass} disabled={busy} onClick={onRetry}>Retry</button>}
        {item.can_stop && <button type="button" className={ghostButtonClass} disabled={busy} onClick={onStop}>Stop</button>}
      </div>
    </li>
  )
}

/** What every process did or is doing, newest first, filtered by process, status and text. */
export function ActivityList({ activity, labels, process, status, q, onFilter, onDone }: {
  activity: UseInfiniteQueryResult<InfiniteData<ActivityPage>>; labels: Labels; process?: ProcessKey; status: ActivityFilter; q: string
  onFilter: (next: { process?: ProcessKey | null; status?: ActivityFilter; q?: string }) => void; onDone: () => void
}) {
  const [text, setText] = useState(q)
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null)
  useEffect(() => { setText(q) }, [q])
  const failed = (error: unknown) => setMessage({ text: failure(error, 'That did not work.'), error: true })
  const retry = useMutation({ mutationFn: (item: ActivityItem) => api.retryProcessItem(item.process, item.id), onSuccess: () => { setMessage({ text: 'Retry scheduled.' }); onDone() }, onError: failed })
  const stop = useMutation({ mutationFn: (item: ActivityItem) => api.stopProcessRun(item.process, item.id), onSuccess: () => { setMessage({ text: 'Stopped.' }); onDone() }, onError: failed })
  const head = activity.data?.pages[0]
  const items = activity.data?.pages.flatMap(page => page.items) ?? []
  const name = (key: ProcessKey) => labels[key]?.label ?? key

  return (
    <GlassPanel aria-label="Activity" className="flex flex-col p-0">
      <div className="flex flex-wrap items-baseline justify-between gap-2 px-6 pt-6">
        <h3 className="text-lg font-semibold text-foreground">Activity</h3>
        <small className="text-xs text-muted-foreground">{process ? `Filtered to ${name(process)}` : 'All processes'}</small>
      </div>
      <div className="flex flex-wrap items-end gap-3 border-b border-border px-6 py-4">
        <label className={cn(labelClass, 'min-w-[180px]')}>Process
          <select className={fieldClass} value={process ?? ''} onChange={event => onFilter({ process: (event.target.value || null) as ProcessKey | null })}>
            <option value="">All processes</option>
            {GROUPS.map(([group, label]) => {
              const keys = PROCESS_KEYS.filter(key => labels[key]?.group === group)
              return keys.length ? <optgroup key={group} label={label}>{keys.map(key => <option key={key} value={key}>{name(key)}</option>)}</optgroup> : null
            })}
            {/* Until the cards arrive, the keys alone, so a process in the URL is still selected. */}
            {Object.keys(labels).length === 0 && PROCESS_KEYS.map(key => <option key={key} value={key}>{key}</option>)}
          </select>
        </label>
        <div role="group" aria-label="Status" className="flex flex-wrap gap-1.5">
          {STATUS_FILTERS.map(([value, label]) => {
            const total = value === 'done' || value === 'all' ? undefined : head?.counts[value]
            return (
              <button key={value} type="button" className={chipClass} aria-pressed={status === value} aria-label={total === undefined ? label : `${label} ${total}`} onClick={() => onFilter({ status: value })}>
                {label}{total !== undefined && <span className="ml-1 font-mono">{count(total)}</span>}
              </button>
            )
          })}
        </div>
        <form className="w-full sm:ml-auto sm:w-auto" role="search" onSubmit={event => { event.preventDefault(); onFilter({ q: text.trim() }) }}>
          <label className={cn(labelClass, 'min-w-[200px]')}>Find
            <input className={fieldClass} type="search" value={text} maxLength={200} placeholder="Article, feed or entity" onChange={event => setText(event.target.value)} />
          </label>
        </form>
      </div>
      {message && <p role="status" className={cn('px-6 pt-3 text-sm', message.error ? 'error text-destructive' : 'text-primary')}>{message.text}</p>}
      {activity.isPending && <Note>Loading the activity…</Note>}
      {activity.isError && <LoadError className="px-6 py-4" query={activity} message="Could not load the activity." />}
      {head && items.length === 0 && <Note>{status === 'attention' && !q ? `Nothing needs attention${process ? ` for ${name(process)}` : ''}.` : `Nothing matches${process ? ` for ${name(process)}` : ''}.`}</Note>}
      {items.length > 0 && (
        <ul aria-label="Activity" className="m-0 list-none p-0">
          {items.map(item => (
            <Row
              key={`${item.process}-${item.id}`} item={item} label={name(item.process)} generatedAt={head!.generated_at} busy={retry.isPending || stop.isPending}
              onRetry={() => retry.mutate(item)}
              onStop={() => { if (window.confirm(`Stop this ${name(item.process)} run? What it already did stays done.`)) stop.mutate(item) }}
            />
          ))}
        </ul>
      )}
      {items.length > 0 && (
        <div className="flex items-center justify-between gap-2.5 border-t border-border px-6 py-3 text-xs text-muted-foreground">
          <span>{`Showing ${count(items.length)}`}</span>
          {activity.hasNextPage && <button type="button" className={ghostButtonClass} disabled={activity.isFetchingNextPage} onClick={() => void activity.fetchNextPage()}>Load more</button>}
        </div>
      )}
    </GlassPanel>
  )
}
