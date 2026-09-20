'use client'

import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { useState } from 'react'
import { api, ApiError } from '../../lib/api'
import type { SavedSearch } from '../../lib/api-types'
import { GlassPanel } from '../../components/GlassPanel'
import { PageHeader } from '../../components/PageHeader'
import { chipClass, fieldClass, ghostButtonClass, primaryButtonClass } from '../../lib/ui-classes'
import { cn } from '../../lib/utils'
import { fromSaved, queryFromState, toHref } from '../../lib/investigation'
import { monitorKeys } from '../../lib/monitors'

export default function SavedSearchesPage() {
  const client = useQueryClient()
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [newName, setNewName] = useState('')
  const pages = useInfiniteQuery({ queryKey: ['saved-searches'], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => api.savedSearches(pageParam), getNextPageParam: page => page.next_cursor ?? undefined })
  const items = pages.data?.pages.flatMap(page => page.items) ?? []
  const refresh = () => client.invalidateQueries({ queryKey: ['saved-searches'] })
  const rename = useMutation({ mutationFn: ({ id, name }: { id: string; name: string }) => api.updateSavedSearch(id, { name }), onSuccess: () => { setRenamingId(null); refresh() } })
  const remove = useMutation({ mutationFn: (id: string) => api.deleteSavedSearch(id), onSuccess: refresh })
  const watch = useMutation({ mutationFn: (item: SavedSearch) => api.createMonitor(item.name, item.state!), onSuccess: () => client.invalidateQueries({ queryKey: monitorKeys.all }) })
  const message = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback
  function openHref(item: SavedSearch) { return item.state ? toHref('/search', queryFromState(fromSaved(item.state))) : '' }
  function startRename(item: SavedSearch) { rename.reset(); setRenamingId(item.id); setNewName(item.name) }
  function submitRename(item: SavedSearch) { if (newName.trim()) rename.mutate({ id: item.id, name: newName.trim() }) }
  function confirmDelete(item: SavedSearch) { if (window.confirm(`Delete the saved search “${item.name}”?`)) remove.mutate(item.id) }

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="Investigations" title="Saved Searches">
        <Link className={chipClass} href="/search">New search</Link>
      </PageHeader>
      <GlassPanel className="overflow-hidden p-0">
        {pages.isPending && <p className="px-6 py-4 text-sm text-muted-foreground">Loading saved searches…</p>}
        {!pages.isPending && pages.isError && <p className="error px-6 py-4 text-sm text-destructive">Could not load saved searches.</p>}
        {!pages.isPending && !pages.isError && !items.length && <p className="px-6 py-4 text-sm text-muted-foreground">No saved searches yet. Save one from Search to reopen the full investigation later.</p>}
        {remove.isError && <p role="alert" className="error px-6 py-4 text-sm text-destructive">{message(remove.error, 'Could not delete this saved search.')}</p>}
        {watch.isError && <p role="alert" className="error px-6 py-4 text-sm text-destructive">{message(watch.error, 'Could not watch this saved search.')}</p>}
        {watch.isSuccess && <p className="px-6 py-4 text-sm text-primary">Watching “{watch.variables.name}”. <Link className={chipClass} href="/monitors/">Open watchlist</Link></p>}
        {items.map(item => (
          <article key={item.id} className="flex flex-wrap items-center justify-between gap-4 border-border px-6 py-4 last:border-b-0">
            <div className="flex flex-col gap-1">
              {renamingId === item.id ? (
                <form className="flex flex-wrap items-end gap-3" onSubmit={event => { event.preventDefault(); submitRename(item) }}>
                  <label className="flex flex-col gap-1.5 text-xs font-medium text-muted-foreground">New name for {item.name}
                    <input className={cn(fieldClass, 'mt-1')} value={newName} onChange={e => setNewName(e.target.value)} maxLength={120} />
                  </label>
                  <div className="flex gap-2">
                    <button type="submit" disabled={rename.isPending} className={primaryButtonClass}>Save name</button>
                    <button type="button" className={ghostButtonClass} onClick={() => setRenamingId(null)}>Cancel</button>
                  </div>
                  {rename.isError && <p role="alert" className="error w-full text-sm text-destructive">{message(rename.error, 'Could not rename this saved search.')}</p>}
                </form>
              ) : (
                <>
                  <strong className="text-[15px] font-semibold text-foreground">{item.name}</strong>
                  <small className="text-xs text-muted-foreground">Updated {new Date(item.updated_at).toLocaleString()}{item.state?.q ? ` · ${item.state.q}` : ''}</small>
                </>
              )}
              {!item.state && <p className="error text-sm text-destructive">This saved search can no longer be opened: {item.problem || 'its stored state is not supported.'}</p>}
            </div>
            <div className="flex items-center gap-2">
              {item.state && <Link className={chipClass} aria-label={`Open ${item.name}`} href={openHref(item)}>Open</Link>}
              {item.state && <button type="button" className={ghostButtonClass} aria-label={`Watch ${item.name}`} disabled={watch.isPending} onClick={() => watch.mutate(item)}>Watch</button>}
              {renamingId !== item.id && <button type="button" className={ghostButtonClass} aria-label={`Rename ${item.name}`} onClick={() => startRename(item)}>Rename</button>}
              <button type="button" className={cn(ghostButtonClass, 'border-destructive/30 text-destructive hover:border-destructive hover:text-destructive')} aria-label={`Delete ${item.name}`} disabled={remove.isPending} onClick={() => confirmDelete(item)}>Delete</button>
            </div>
          </article>
        ))}
        {pages.hasNextPage && <div className="px-6 py-4"><button className={ghostButtonClass} disabled={pages.isFetchingNextPage} onClick={() => pages.fetchNextPage()}>{pages.isFetchingNextPage ? 'Loading…' : 'Load more saved searches'}</button></div>}
      </GlassPanel>
    </div>
  )
}
