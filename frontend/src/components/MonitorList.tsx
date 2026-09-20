'use client'

import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { api, ApiError } from '../lib/api'
import type { Monitor } from '../lib/api-types'
import { countsLabel, monitorKeys, refetchEvery, statusText, when } from '../lib/monitors'
import { toHref } from '../lib/investigation'
import { chipClass, fieldClass, ghostButtonClass, labelClass } from '../lib/ui-classes'
import { cn } from '../lib/utils'
import { GlassPanel } from './GlassPanel'
import { PageHeader } from './PageHeader'

const Note = ({ children, error }: { children: string; error?: boolean }) => <p className={cn('px-6 py-4 text-sm', error ? 'error text-destructive' : 'text-muted-foreground')}>{children}</p>

export function MonitorList() {
  const router = useRouter()
  const pathname = usePathname()
  const params = useSearchParams()
  const client = useQueryClient()
  const order = params.get('order') === 'name' ? 'name' : 'activity'
  const pages = useInfiniteQuery({
    queryKey: monitorKeys.list(order), initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => api.monitors(order, pageParam), getNextPageParam: page => page.next_cursor ?? undefined,
    refetchInterval: query => refetchEvery(query.state.data?.pages.flatMap(page => page.items)),
  })
  const items = pages.data?.pages.flatMap(page => page.items) ?? []
  const refresh = (id: string) => { client.invalidateQueries({ queryKey: ['monitors', 'list'] }); client.invalidateQueries({ queryKey: monitorKeys.detail(id) }) }
  const toggle = useMutation({ mutationFn: (item: Monitor) => api.updateMonitor(item.id, { enabled: !item.enabled }), onSuccess: (_, item) => refresh(item.id) })
  const remove = useMutation({ mutationFn: (item: Monitor) => api.deleteMonitor(item.id), onSuccess: (_, item) => refresh(item.id) })
  const message = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback
  const failure = toggle.error ?? remove.error

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="Investigations" title="Watchlist">
        <div className="flex flex-wrap items-end gap-3">
          <label className={labelClass}>Order
            <select className={fieldClass} value={order} onChange={event => router.push(toHref(pathname, new URLSearchParams(event.target.value === 'name' ? { order: 'name' } : {})))}>
              <option value="activity">Recent activity</option>
              <option value="name">Name</option>
            </select>
          </label>
          <Link className={chipClass} href="/search/">Watch a search</Link>
        </div>
      </PageHeader>
      <GlassPanel className="overflow-hidden p-0">
        {pages.isPending && <Note>Loading monitors…</Note>}
        {!pages.isPending && pages.isError && <Note error>Could not load monitors.</Note>}
        {!pages.isPending && !pages.isError && !items.length && <Note>No monitors yet. Watch a search from Search or Saved Searches to see new matches here.</Note>}
        {failure && <p role="alert" className="error px-6 py-4 text-sm text-destructive">{message(failure, 'Could not change this monitor.')}</p>}
        {items.map(item => {
          const status = statusText(item)
          return (
            <article key={item.id} aria-label={item.name} data-unseen={item.unseen_article_count > 0} className="flex flex-wrap items-center justify-between gap-4 border-border px-6 py-4 last:border-b-0 data-[unseen=true]:border-l-2 data-[unseen=true]:border-l-primary">
              <div className="flex flex-col gap-1">
                <strong className="text-[15px] font-semibold text-foreground">{item.name}</strong>
                <span className={cn('w-fit rounded-full px-2.5 py-1 text-xs font-medium', item.unseen_article_count > 0 ? 'bg-primary/15 text-primary' : 'bg-white/8 text-muted-foreground')}>
                  {item.unseen_article_count > 0 ? countsLabel(item) : 'Nothing new'}
                </span>
                {status && <p className={cn('text-sm', item.error_category || !item.state ? 'error text-destructive' : 'text-muted-foreground')}>{status}</p>}
                <small className="text-xs text-muted-foreground">
                  {item.kind}{item.evaluated_through ? ` · Counted through ${when(item.evaluated_through)}` : ''}{item.last_evaluated_at ? ` · Last checked ${when(item.last_evaluated_at)}` : ''}{item.latest_match_at ? ` · Latest match ${when(item.latest_match_at)}` : ''}
                </small>
              </div>
              <div className="flex items-center gap-2">
                <Link className={chipClass} aria-label={`Open ${item.name}`} href={toHref(pathname, new URLSearchParams({ id: item.id }))}>Open</Link>
                {item.state && <button type="button" className={ghostButtonClass} aria-label={`${item.enabled ? 'Pause' : 'Resume'} ${item.name}`} disabled={toggle.isPending} onClick={() => toggle.mutate(item)}>{item.enabled ? 'Pause' : 'Resume'}</button>}
                <button type="button" className={cn(ghostButtonClass, 'border-destructive/30 text-destructive hover:border-destructive hover:text-destructive')} aria-label={`Delete ${item.name}`} disabled={remove.isPending} onClick={() => { if (window.confirm(`Delete the monitor “${item.name}”?`)) remove.mutate(item) }}>Delete</button>
              </div>
            </article>
          )
        })}
        {pages.hasNextPage && <div className="px-6 py-4"><button className={ghostButtonClass} disabled={pages.isFetchingNextPage} onClick={() => pages.fetchNextPage()}>{pages.isFetchingNextPage ? 'Loading…' : 'Load more monitors'}</button></div>}
      </GlassPanel>
    </div>
  )
}
