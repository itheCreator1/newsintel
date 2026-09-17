'use client'

import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { useState } from 'react'
import { api, ApiError } from '../../lib/api'
import type { SavedSearch } from '../../lib/api-types'
import { fromSaved, queryFromState, toHref } from '../../lib/investigation'

export default function SavedSearchesPage() {
  const client = useQueryClient()
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [newName, setNewName] = useState('')
  const pages = useInfiniteQuery({ queryKey: ['saved-searches'], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => api.savedSearches(pageParam), getNextPageParam: page => page.next_cursor ?? undefined })
  const items = pages.data?.pages.flatMap(page => page.items) ?? []
  const refresh = () => client.invalidateQueries({ queryKey: ['saved-searches'] })
  const rename = useMutation({ mutationFn: ({ id, name }: { id: string; name: string }) => api.updateSavedSearch(id, { name }), onSuccess: () => { setRenamingId(null); refresh() } })
  const remove = useMutation({ mutationFn: (id: string) => api.deleteSavedSearch(id), onSuccess: refresh })
  const message = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback
  function openHref(item: SavedSearch) { return item.state ? toHref('/search', queryFromState(fromSaved(item.state))) : '' }
  function startRename(item: SavedSearch) { rename.reset(); setRenamingId(item.id); setNewName(item.name) }
  function submitRename(item: SavedSearch) { if (newName.trim()) rename.mutate({ id: item.id, name: newName.trim() }) }
  function confirmDelete(item: SavedSearch) { if (window.confirm(`Delete the saved search “${item.name}”?`)) remove.mutate(item.id) }

  return (
    <>
      <header><div><p className="eyebrow">Investigations</p><h2>Saved Searches</h2></div><Link className="secondary" href="/search">New search</Link></header>
      <section className="panel saved-searches">
        {pages.isPending && <p className="muted">Loading saved searches…</p>}
        {!pages.isPending && pages.isError && <p className="error">Could not load saved searches.</p>}
        {!pages.isPending && !pages.isError && !items.length && <p className="muted">No saved searches yet. Save one from Search to reopen the full investigation later.</p>}
        {remove.isError && <p role="alert" className="error">{message(remove.error, 'Could not delete this saved search.')}</p>}
        {items.map(item => (
          <article key={item.id} className="saved-search-row">
            <div>
              {renamingId === item.id ? (
                <form className="rename-form" onSubmit={event => { event.preventDefault(); submitRename(item) }}>
                  <label>New name for {item.name}<input value={newName} onChange={e => setNewName(e.target.value)} maxLength={120} /></label>
                  <div className="actions">
                    <button type="submit" disabled={rename.isPending}>Save name</button>
                    <button type="button" className="secondary" onClick={() => setRenamingId(null)}>Cancel</button>
                  </div>
                  {rename.isError && <p role="alert" className="error">{message(rename.error, 'Could not rename this saved search.')}</p>}
                </form>
              ) : (
                <>
                  <strong>{item.name}</strong>
                  <small>Updated {new Date(item.updated_at).toLocaleString()}{item.state?.q ? ` · ${item.state.q}` : ''}</small>
                </>
              )}
              {!item.state && <p className="error">This saved search can no longer be opened: {item.problem || 'its stored state is not supported.'}</p>}
            </div>
            <div className="actions">
              {item.state && <Link className="annotation-link" aria-label={`Open ${item.name}`} href={openHref(item)}>Open</Link>}
              {renamingId !== item.id && <button type="button" className="secondary" aria-label={`Rename ${item.name}`} onClick={() => startRename(item)}>Rename</button>}
              <button type="button" className="danger" aria-label={`Delete ${item.name}`} disabled={remove.isPending} onClick={() => confirmDelete(item)}>Delete</button>
            </div>
          </article>
        ))}
        {pages.hasNextPage && <button className="secondary" disabled={pages.isFetchingNextPage} onClick={() => pages.fetchNextPage()}>{pages.isFetchingNextPage ? 'Loading…' : 'Load more saved searches'}</button>}
      </section>
    </>
  )
}
