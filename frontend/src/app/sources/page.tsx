'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../../lib/api'
import type { Feed } from '../../lib/api-types'
import { GlassPanel } from '../../components/GlassPanel'
import { PageHeader } from '../../components/PageHeader'
import { StatusBadge } from '../../components/StatusBadge'
import { fieldClass, ghostButtonClass, labelClass, primaryButtonClass } from '../../lib/ui-classes'
import { cn } from '../../lib/utils'

export default function SourcesPage() {
  const client = useQueryClient()
  const [cursor, setCursor] = useState<string | undefined>()
  const [selected, setSelected] = useState<Feed>()
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [country, setCountry] = useState('')
  const [language, setLanguage] = useState('')
  const [pollIntervalMinutes, setPollIntervalMinutes] = useState(30)
  const [mode, setMode] = useState<'rss' | 'full_text' | 'full_text_html'>('rss')

  const feeds = useQuery({ queryKey: ['feeds', cursor], queryFn: () => api.feeds(cursor) })
  const save = useMutation({
    mutationFn: () => api.createFeed({ name, url, source_country: country || undefined, expected_language: language || undefined, poll_interval_minutes: pollIntervalMinutes, fetching_mode: mode }),
    onSuccess: () => { setName(''); setUrl(''); client.invalidateQueries({ queryKey: ['feeds'] }) },
  })
  const update = useMutation({
    mutationFn: ({ feed, changes }: { feed: Feed; changes: Partial<Feed> }) => api.updateFeed(feed.id, changes),
    onSuccess: () => client.invalidateQueries({ queryKey: ['feeds'] }),
  })
  const poll = useMutation({ mutationFn: api.pollFeed, onSuccess: () => client.invalidateQueries({ queryKey: ['feeds'] }) })
  const retire = useMutation({
    mutationFn: api.retireFeed,
    onSuccess: () => { setSelected(undefined); client.invalidateQueries({ queryKey: ['feeds'] }) },
  })
  const history = useQuery({ queryKey: ['fetches', selected?.id], queryFn: () => api.fetches(selected!.id), enabled: Boolean(selected) })

  function confirmRetire(feed: Feed) { if (window.confirm('Retire this source? Its archive will be preserved.')) retire.mutate(feed.id) }

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="Collection" title="Sources" />
      <p className="-mt-2 text-sm text-muted-foreground">Choose RSS metadata, readable full text, or full text with retained source HTML for each source.</p>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
        <GlassPanel className="flex flex-col gap-4">
          <h3 className="text-sm font-semibold text-foreground">Add source</h3>
          <form className="flex flex-col gap-4" onSubmit={event => { event.preventDefault(); save.mutate() }}>
            <label className={labelClass}>Name<input className={cn(fieldClass, 'mt-1')} value={name} onChange={event => setName(event.target.value)} required maxLength={200} /></label>
            <label className={labelClass}>Feed URL<input className={cn(fieldClass, 'mt-1')} value={url} onChange={event => setUrl(event.target.value)} required type="url" /></label>
            <div className="grid grid-cols-2 gap-4">
              <label className={labelClass}>Source country<input className={cn(fieldClass, 'mt-1')} value={country} onChange={event => setCountry(event.target.value)} maxLength={2} placeholder="GR" /></label>
              <label className={labelClass}>Expected language<input className={cn(fieldClass, 'mt-1')} value={language} onChange={event => setLanguage(event.target.value)} maxLength={16} placeholder="el" /></label>
            </div>
            <label className={labelClass}>Collection mode
              <select className={cn(fieldClass, 'mt-1')} value={mode} onChange={event => setMode(event.target.value as typeof mode)}>
                <option value="rss">RSS metadata only</option>
                <option value="full_text">Readable full text</option>
                <option value="full_text_html">Full text + retained HTML</option>
              </select>
            </label>
            <label className={labelClass}>Poll interval (minutes)<input className={cn(fieldClass, 'mt-1')} value={pollIntervalMinutes} onChange={event => setPollIntervalMinutes(Number(event.target.value))} type="number" min={5} /></label>
            <button className={cn(primaryButtonClass, 'self-start')}>Add source</button>
          </form>
        </GlassPanel>

        <GlassPanel className="overflow-hidden p-0">
          <h3 className="px-6 pt-6 text-sm font-semibold text-foreground">Managed sources</h3>
          {feeds.isPending && <p className="px-6 py-4 text-sm text-muted-foreground">Loading…</p>}
          {!feeds.isPending && !feeds.data?.items.length && <p className="px-6 py-4 text-sm text-muted-foreground">No sources yet.</p>}
          <div className="mt-2 flex flex-col">
            {feeds.data?.items.map(feed => (
              <article key={feed.id} className="source-row flex cursor-pointer flex-col gap-3 border-t border-border px-6 py-4 hover:bg-accent/40" onClick={() => setSelected(feed)}>
                <div className="flex items-start justify-between gap-4">
                  <div className="flex flex-col gap-0.5">
                    <strong className="text-[15px] font-semibold text-foreground">{feed.name}</strong>
                    <small className="break-all text-xs text-muted-foreground">{feed.url}</small>
                    <small className="text-xs text-muted-foreground">{feed.fetching_mode.split('_').join(' ')}</small>
                  </div>
                  <StatusBadge tone={feed.last_success_at ? 'healthy' : 'pending'}>{feed.enabled ? (feed.last_success_at ? 'Healthy' : 'Awaiting poll') : 'Disabled'}</StatusBadge>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <select
                    className={cn(fieldClass, 'w-auto')}
                    value={feed.fetching_mode}
                    aria-label="Collection mode"
                    onClick={event => event.stopPropagation()}
                    onChange={event => update.mutate({ feed, changes: { fetching_mode: event.target.value as Feed['fetching_mode'] } })}
                  >
                    <option value="rss">RSS only</option>
                    <option value="full_text">Full text</option>
                    <option value="full_text_html">Full text + HTML</option>
                  </select>
                  <button type="button" className={ghostButtonClass} onClick={event => { event.stopPropagation(); update.mutate({ feed, changes: { enabled: !feed.enabled } }) }}>{feed.enabled ? 'Disable' : 'Enable'}</button>
                  <button type="button" className={ghostButtonClass} onClick={event => { event.stopPropagation(); poll.mutate(feed.id) }}>Poll now</button>
                  <button type="button" className={cn(ghostButtonClass, 'border-destructive/30 text-destructive hover:border-destructive hover:text-destructive')} onClick={event => { event.stopPropagation(); confirmRetire(feed) }}>Retire</button>
                </div>
              </article>
            ))}
          </div>
          {feeds.data?.next_cursor && <div className="border-t border-border px-6 py-4"><button type="button" className={ghostButtonClass} onClick={() => setCursor(feeds.data?.next_cursor ?? undefined)}>Next page</button></div>}
        </GlassPanel>
      </div>

      {selected && (
        <GlassPanel className="overflow-hidden p-0">
          <h3 className="px-6 pt-6 text-sm font-semibold text-foreground">{selected.name} fetch history</h3>
          {!history.data?.items.length && <p className="px-6 py-4 text-sm text-muted-foreground">No fetch attempts.</p>}
          <div className="mt-2 flex flex-col">
            {history.data?.items.map(fetch => (
              <article key={fetch.id} className="grid grid-cols-1 gap-1 border-t border-border px-6 py-4 sm:grid-cols-3">
                <strong className="text-sm font-semibold text-foreground">{fetch.status}</strong>
                <span className="text-sm text-muted-foreground">{new Date(fetch.started_at).toLocaleString()}</span>
                <span className="text-sm text-muted-foreground">{fetch.new_article_count} new / {fetch.entry_count} entries</span>
                {fetch.error_message && <span className="error text-sm text-destructive sm:col-span-3">{fetch.error_category}: {fetch.error_message}</span>}
              </article>
            ))}
          </div>
        </GlassPanel>
      )}
    </div>
  )
}
