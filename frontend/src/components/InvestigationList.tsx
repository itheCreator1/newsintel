'use client'

import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { Investigation, MonitorKind } from '../lib/api-types'
import { fromSaved, queryFromState, toHref } from '../lib/investigation'
import { countsLabel, monitorKeys, refetchEvery, statusText, when } from '../lib/monitors'
import { chipClass, fieldClass, ghostButtonClass, labelClass, primaryButtonClass } from '../lib/ui-classes'
import { cn } from '../lib/utils'
import { GlassPanel } from './GlassPanel'
import { PageHeader } from './PageHeader'
import { LoadError, Note } from './Feedback'

const KIND_LABELS: Record<MonitorKind, string> = { search: 'Search', entity: 'Entity', source: 'Source', country: 'Country', cluster: 'Story', event: 'Event' }

const searchHref = (item: Investigation) => item.saved_search?.state ? toHref('/search', queryFromState(fromSaved(item.saved_search.state))) : null
const monitorHref = (id: string) => toHref('/monitors', new URLSearchParams({ id }))
// Watching needs a state that still reads: the monitor's own once there is one, else the saved search's.
const canWatch = (item: Investigation) => Boolean(item.monitor ? item.monitor.state : item.saved_search?.state)
const watched = (item: Investigation) => Boolean(item.monitor?.enabled)

/** Saved searches and monitors in one list; a saved search watched in place is one row with a Watch switch. */
export function InvestigationList() {
  const router = useRouter()
  const pathname = usePathname()
  const params = useSearchParams()
  const client = useQueryClient()
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [newName, setNewName] = useState('')
  const order = params.get('order') === 'name' ? 'name' : 'activity'
  const pages = useInfiniteQuery({
    queryKey: ['investigations', order], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => api.investigations(order, pageParam), getNextPageParam: page => page.next_cursor ?? undefined,
    refetchInterval: query => refetchEvery(query.state.data?.pages.flatMap(page => page.items.flatMap(item => item.monitor ? [item.monitor] : []))),
  })
  const items = pages.data?.pages.flatMap(page => page.items) ?? []
  const refresh = () => { client.invalidateQueries({ queryKey: ['investigations'] }); client.invalidateQueries({ queryKey: monitorKeys.all }); client.invalidateQueries({ queryKey: ['saved-searches'] }) }
  const watch = useMutation({
    // A monitor pauses or resumes in place; a saved search not yet watched gets its monitor.
    mutationFn: (item: Investigation) => item.monitor ? api.updateMonitor(item.monitor.id, { enabled: !item.monitor.enabled }) : api.watchSavedSearch(item.saved_search!.id),
    onSuccess: refresh,
  })
  const rename = useMutation({
    mutationFn: ({ item, name }: { item: Investigation; name: string }) => item.saved_search ? api.updateSavedSearch(item.saved_search.id, { name }) : api.updateMonitor(item.monitor!.id, { name }),
    onSuccess: () => { setRenamingId(null); refresh() },
  })
  const remove = useMutation({ mutationFn: (item: Investigation) => item.saved_search ? api.deleteSavedSearch(item.saved_search.id) : api.deleteMonitor(item.monitor!.id), onSuccess: refresh })
  const message = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback
  const failure = watch.error ?? remove.error

  function startRename(item: Investigation) { rename.reset(); setRenamingId(item.id); setNewName(item.name) }
  function submitRename(item: Investigation) { if (newName.trim()) rename.mutate({ item, name: newName.trim() }) }
  function confirmDelete(item: Investigation) {
    const question = item.saved_search && item.monitor ? `Delete “${item.name}” and stop watching it?` : `Delete “${item.name}”?`
    if (window.confirm(question)) remove.mutate(item)
  }

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="Explore" title="Investigations" description="Saved searches and the ones you watch. A watched investigation is checked for new articles on its own.">
        <div className="flex flex-wrap items-end gap-3">
          <label className={labelClass}>Order
            <select className={fieldClass} value={order} onChange={event => router.push(toHref(pathname, new URLSearchParams(event.target.value === 'name' ? { order: 'name' } : {})))}>
              <option value="activity">Recent activity</option>
              <option value="name">Name</option>
            </select>
          </label>
          <Link className={chipClass} href="/search/">New search</Link>
        </div>
      </PageHeader>
      <GlassPanel className="overflow-hidden p-0">
        {pages.isPending && <Note>Loading investigations…</Note>}
        {!pages.isPending && pages.isError && <LoadError className="px-6 py-4" query={pages} message="Could not load investigations." />}
        {!pages.isPending && !pages.isError && !items.length && <Note>No investigations yet. Save or watch a search from Search to find it here.</Note>}
        {failure && <p role="alert" className="error px-6 py-4 text-sm text-destructive">{message(failure, 'Could not change this investigation.')}</p>}
        {items.map(item => {
          const monitor = item.monitor
          const status = monitor ? statusText(monitor) : null
          const unseen = (monitor?.unseen_article_count ?? 0) > 0
          const search = searchHref(item)
          const open = monitor ? monitorHref(monitor.id) : search
          return (
            <article key={item.id} aria-label={item.name} data-unseen={unseen} className="flex flex-wrap items-center justify-between gap-4 border-border px-6 py-4 last:border-b-0 data-[unseen=true]:border-l-2 data-[unseen=true]:border-l-primary">
              <div className="flex flex-col gap-1">
                {renamingId === item.id ? (
                  <form className="flex flex-wrap items-end gap-3" onSubmit={event => { event.preventDefault(); submitRename(item) }}>
                    <label className={labelClass}>New name for {item.name}
                      <input className={cn(fieldClass, 'mt-1')} value={newName} onChange={event => setNewName(event.target.value)} maxLength={120} />
                    </label>
                    <div className="flex gap-2">
                      <button type="submit" disabled={rename.isPending} className={primaryButtonClass}>Save name</button>
                      <button type="button" className={ghostButtonClass} onClick={() => setRenamingId(null)}>Cancel</button>
                    </div>
                    {rename.isError && <p role="alert" className="error w-full text-sm text-destructive">{message(rename.error, 'Could not rename this investigation.')}</p>}
                  </form>
                ) : <strong className="text-[15px] font-semibold text-foreground">{item.name}</strong>}
                <span className="flex flex-wrap items-center gap-2">
                  <span className="rounded-full border border-border px-2.5 py-1 text-xs font-medium text-muted-foreground">{KIND_LABELS[monitor?.kind ?? 'search']}</span>
                  <span className={cn('w-fit rounded-full px-2.5 py-1 text-xs font-medium', unseen ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground')}>
                    {!monitor ? 'Not watched' : unseen ? countsLabel(monitor) : 'Nothing new'}
                  </span>
                </span>
                {status && <p className={cn('text-sm', monitor?.error_category || !monitor?.state ? 'error text-destructive' : 'text-muted-foreground')}>{status}</p>}
                {item.saved_search && !item.saved_search.state && <p className="error text-sm text-destructive">This saved search can no longer be opened: {item.saved_search.problem || 'its stored state is not supported.'}</p>}
                <small className="text-xs text-muted-foreground">
                  {monitor
                    ? `${monitor.evaluated_through ? `Counted through ${when(monitor.evaluated_through)}` : 'Not counted yet'}${monitor.last_evaluated_at ? ` · Last checked ${when(monitor.last_evaluated_at)}` : ''}${monitor.latest_match_at ? ` · Latest match ${when(monitor.latest_match_at)}` : ''}`
                    : `Updated ${when(item.saved_search?.updated_at)}${item.saved_search?.state?.q ? ` · ${item.saved_search.state.q}` : ''}`}
                </small>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {canWatch(item) && (
                  <button type="button" role="switch" aria-checked={watched(item)} aria-label={`Watch ${item.name}`} disabled={watch.isPending} onClick={() => watch.mutate(item)}
                    className={cn(ghostButtonClass, 'gap-2', watched(item) && 'border-ring text-foreground')}>
                    <span aria-hidden="true" className={cn('relative inline-block h-4 w-7 rounded-full transition-colors', watched(item) ? 'bg-primary' : 'bg-muted-foreground/40')}>
                      <span className={cn('absolute top-0.5 size-3 rounded-full bg-background transition-all', watched(item) ? 'left-3.5' : 'left-0.5')} />
                    </span>
                    Watch
                  </button>
                )}
                {open && <Link className={chipClass} aria-label={`Open ${item.name}`} href={open}>Open</Link>}
                {monitor && search && <Link className={chipClass} aria-label={`Search ${item.name}`} href={search}>Search</Link>}
                {renamingId !== item.id && <button type="button" className={ghostButtonClass} aria-label={`Rename ${item.name}`} onClick={() => startRename(item)}>Rename</button>}
                <button type="button" className={cn(ghostButtonClass, 'border-destructive/30 text-destructive hover:border-destructive hover:text-destructive')} aria-label={`Delete ${item.name}`} disabled={remove.isPending} onClick={() => confirmDelete(item)}>Delete</button>
              </div>
            </article>
          )
        })}
        {pages.hasNextPage && <div className="px-6 py-4"><button className={ghostButtonClass} disabled={pages.isFetchingNextPage} onClick={() => pages.fetchNextPage()}>{pages.isFetchingNextPage ? 'Loading…' : 'Load more investigations'}</button></div>}
      </GlassPanel>
    </div>
  )
}
