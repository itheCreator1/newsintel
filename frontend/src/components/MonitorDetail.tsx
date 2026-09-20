'use client'

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { Monitor, MonitorResult } from '../lib/api-types'
import { clusterHref, fromSaved, queryFromState, toHref } from '../lib/investigation'
import { countsLabel, monitorKeys, monitorStatus, refetchEvery, statusText, when } from '../lib/monitors'
import { chipClass, fieldClass, ghostButtonClass, labelClass, primaryButtonClass } from '../lib/ui-classes'
import { cn, plural } from '../lib/utils'
import { GlassPanel } from './GlassPanel'
import { MonitorChanges } from './MonitorChanges'
import { PageHeader } from './PageHeader'

const message = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback
const Note = ({ children, error }: { children: string; error?: boolean }) => <p className={cn('px-6 py-4 text-sm', error ? 'error text-destructive' : 'text-muted-foreground')}>{children}</p>

// ponytail: a small row of its own instead of extracting Search's inline result card; share it when a third list needs it.
function ResultRow({ result, currentHref }: { result: MonitorResult; currentHref: string }) {
  const otherSources = (result.story_cluster?.source_count ?? 0) - result.distinct_source_count
  return (
    <article className="border-border px-6 py-4 last:border-b-0 hover:bg-accent/40">
      <Link className="text-[15px] font-semibold text-foreground hover:underline" href={toHref('/articles', new URLSearchParams({ article: result.article_id, from: currentHref }))}>{result.title}</Link>
      <p className="text-xs text-muted-foreground">{when(result.effective_date)} · {plural(result.distinct_source_count, 'source')}</p>
      <div className="mt-2 flex flex-wrap gap-2">
        {result.source_refs.map(source => <span key={source.id} className={chipClass}>{source.name}</span>)}
        {result.story_cluster && otherSources > 0 && <Link className={chipClass} href={clusterHref(result.story_cluster.id, currentHref)}>Also reported by {plural(otherSources, 'other source')}</Link>}
      </div>
    </article>
  )
}

export function MonitorDetail({ id }: { id: string }) {
  const router = useRouter()
  const pathname = usePathname()
  const params = useSearchParams()
  const client = useQueryClient()
  const currentHref = toHref(pathname, params)
  const scope = params.get('scope') === 'recent' ? 'recent' : 'unseen'
  const [renaming, setRenaming] = useState(false)
  const [newName, setNewName] = useState('')

  const detail = useQuery({ queryKey: monitorKeys.detail(id), queryFn: () => api.monitor(id), retry: false, refetchInterval: query => refetchEvery(query.state.data ? [query.state.data] : undefined) })
  const item = detail.data
  const readable = Boolean(item?.state)
  const results = useInfiniteQuery({
    queryKey: monitorKeys.results(id, scope), initialPageParam: undefined as string | undefined, enabled: readable, retry: false,
    queryFn: ({ pageParam }) => api.monitorResults(id, scope, pageParam), getNextPageParam: page => page.next_cursor ?? undefined,
  })
  // A refetch chains fresh cursors from the first page, so the list (and the change summary over the same window) follows the counters without mixing windows.
  const marker = item ? `${item.evaluated_through}|${item.viewed_through}|${item.unseen_article_count}` : ''
  const seenMarker = useRef(marker)
  useEffect(() => {
    if (marker && seenMarker.current && seenMarker.current !== marker) {
      client.invalidateQueries({ queryKey: ['monitors', 'results', id] })
      client.invalidateQueries({ queryKey: monitorKeys.changes(id) })
    }
    seenMarker.current = marker
  }, [marker, id, client])

  const store = (updated: Monitor) => { client.setQueryData(monitorKeys.detail(id), updated); client.invalidateQueries({ queryKey: ['monitors', 'list'] }) }
  const rename = useMutation({ mutationFn: (name: string) => api.updateMonitor(id, { name }), onSuccess: updated => { setRenaming(false); store(updated) } })
  const toggle = useMutation({ mutationFn: (enabled: boolean) => api.updateMonitor(id, { enabled }), onSuccess: store })
  const markSeen = useMutation({ mutationFn: (through: string) => api.markMonitorViewed(id, through), onSuccess: store, onError: () => client.invalidateQueries({ queryKey: monitorKeys.detail(id) }) })
  const remove = useMutation({ mutationFn: () => api.deleteMonitor(id), onSuccess: () => { client.invalidateQueries({ queryKey: ['monitors', 'list'] }); router.push('/monitors/') } })

  const rows = results.data?.pages.flatMap(page => page.items) ?? []
  // The boundary of the list the analyst is looking at, never a fresher one: the API recounts anything after it.
  const through = results.data?.pages[0]?.window_end ?? null
  const status = item ? monitorStatus(item) : null
  const emptyText = status === 'pending' ? 'Nothing listed yet — the counts are being refreshed.' : status === 'waiting' ? 'Results appear after the first check.' : scope === 'unseen' ? 'Nothing new since you last looked.' : 'No matches yet.'
  const scopeHref = (next: 'unseen' | 'recent') => toHref(pathname, new URLSearchParams({ id, ...(next === 'recent' ? { scope: 'recent' } : {}) }))
  const notice = item && statusText(item)

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="Watchlist" title="Monitor"><Link className={chipClass} href="/monitors/">Back to watchlist</Link></PageHeader>
      <GlassPanel className="flex flex-col gap-3">
        {detail.isPending && <p className="text-sm text-muted-foreground">Loading monitor…</p>}
        {detail.isError && <p className="error text-sm text-destructive">{detail.error instanceof ApiError && detail.error.status === 404 ? 'Monitor not found.' : 'Could not load this monitor.'}</p>}
        {item && (
          <>
            {renaming ? (
              <form className="flex flex-wrap items-end gap-3" onSubmit={event => { event.preventDefault(); if (newName.trim()) rename.mutate(newName.trim()) }}>
                <label className={labelClass}>New name for {item.name}<input className={cn(fieldClass, 'mt-1')} value={newName} onChange={e => setNewName(e.target.value)} maxLength={120} /></label>
                <button type="submit" disabled={rename.isPending} className={primaryButtonClass}>Save name</button>
                <button type="button" className={ghostButtonClass} onClick={() => setRenaming(false)}>Cancel</button>
                {rename.isError && <p role="alert" className="error w-full text-sm text-destructive">{message(rename.error, 'Could not rename this monitor.')}</p>}
              </form>
            ) : <h3 className="text-lg font-semibold text-foreground">{item.name}</h3>}
            <p className="text-sm text-foreground">{item.unseen_article_count > 0 ? countsLabel(item) : 'Nothing new'}</p>
            {notice && <p className={cn('text-sm', item.error_category || !item.state ? 'error text-destructive' : 'text-muted-foreground')}>{notice}</p>}
            <p className="text-xs text-muted-foreground">
              {item.kind}{item.evaluated_through ? ` · Counted through ${when(item.evaluated_through)}` : ''} · Last checked {when(item.last_evaluated_at)}{item.enabled ? ` · Next check ${when(item.next_evaluation_at)}` : ''}{item.latest_match_at ? ` · Latest match ${when(item.latest_match_at)}` : ''}
            </p>
            <div className="flex flex-wrap gap-2">
              {item.state && <Link className={chipClass} href={toHref('/search', queryFromState(fromSaved(item.state)))}>Open in search</Link>}
              {item.state && <button type="button" className={ghostButtonClass} disabled={toggle.isPending} onClick={() => toggle.mutate(!item.enabled)}>{item.enabled ? 'Pause monitor' : 'Resume monitor'}</button>}
              {!renaming && <button type="button" className={ghostButtonClass} onClick={() => { rename.reset(); setNewName(item.name); setRenaming(true) }}>Rename monitor</button>}
              <button type="button" className={cn(ghostButtonClass, 'border-destructive/30 text-destructive hover:border-destructive hover:text-destructive')} disabled={remove.isPending} onClick={() => { if (window.confirm(`Delete the monitor “${item.name}”?`)) remove.mutate() }}>Delete monitor</button>
            </div>
            {(toggle.isError || remove.isError) && <p role="alert" className="error text-sm text-destructive">{message(toggle.error ?? remove.error, 'Could not change this monitor.')}</p>}
          </>
        )}
      </GlassPanel>

      {readable && item && scope === 'unseen' && <MonitorChanges id={id} item={item} currentHref={currentHref} />}

      {readable && (
        <GlassPanel className="overflow-hidden p-0">
          <div className="flex flex-wrap items-center justify-between gap-3 px-6 pt-4">
            <div className="flex gap-2">
              <button type="button" aria-pressed={scope === 'unseen'} className={cn(ghostButtonClass, scope === 'unseen' && 'border-ring text-foreground')} onClick={() => router.push(scopeHref('unseen'))}>New since last view</button>
              <button type="button" aria-pressed={scope === 'recent'} className={cn(ghostButtonClass, scope === 'recent' && 'border-ring text-foreground')} onClick={() => router.push(scopeHref('recent'))}>Recent matches</button>
            </div>
            {scope === 'unseen' && (
              <button type="button" className={primaryButtonClass} disabled={!through || !item?.unseen_article_count || markSeen.isPending} onClick={() => through && markSeen.mutate(through)}>Mark as seen ({item?.unseen_article_count ?? 0})</button>
            )}
          </div>
          {markSeen.isError && <p role="alert" className="error px-6 py-4 text-sm text-destructive">{message(markSeen.error, 'Could not mark these as seen.')}</p>}
          {results.isPending && <Note>Loading articles…</Note>}
          {!results.isPending && results.isError && <Note error>Could not load articles.</Note>}
          {!results.isPending && !results.isError && !rows.length && <Note>{emptyText}</Note>}
          {rows.map(result => <ResultRow key={result.article_id} result={result} currentHref={currentHref} />)}
          {results.hasNextPage && <div className="px-6 py-4"><button className={ghostButtonClass} disabled={results.isFetchingNextPage} onClick={() => results.fetchNextPage()}>{results.isFetchingNextPage ? 'Loading…' : 'Load more articles'}</button></div>}
        </GlassPanel>
      )}
    </div>
  )
}
