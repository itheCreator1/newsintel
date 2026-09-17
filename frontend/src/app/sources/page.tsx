'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../../lib/api'
import type { Feed } from '../../lib/api-types'

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
    <>
      <header><div><p className="eyebrow">Collection</p><h2>Sources</h2></div></header>
      <p className="lede">Choose RSS metadata, readable full text, or full text with retained source HTML for each source.</p>
      <div className="two-column">
        <section className="panel">
          <h3>Add source</h3>
          <form onSubmit={event => { event.preventDefault(); save.mutate() }}>
            <label>Name<input value={name} onChange={event => setName(event.target.value)} required maxLength={200} /></label>
            <label>Feed URL<input value={url} onChange={event => setUrl(event.target.value)} required type="url" /></label>
            <div className="form-row">
              <label>Source country<input value={country} onChange={event => setCountry(event.target.value)} maxLength={2} placeholder="GR" /></label>
              <label>Expected language<input value={language} onChange={event => setLanguage(event.target.value)} maxLength={16} placeholder="el" /></label>
            </div>
            <label>Collection mode
              <select value={mode} onChange={event => setMode(event.target.value as typeof mode)}>
                <option value="rss">RSS metadata only</option>
                <option value="full_text">Readable full text</option>
                <option value="full_text_html">Full text + retained HTML</option>
              </select>
            </label>
            <label>Poll interval (minutes)<input value={pollIntervalMinutes} onChange={event => setPollIntervalMinutes(Number(event.target.value))} type="number" min={5} /></label>
            <button>Add source</button>
          </form>
        </section>
        <section className="panel">
          <h3>Managed sources</h3>
          {feeds.isPending && <p>Loading…</p>}
          {!feeds.isPending && !feeds.data?.items.length && <p className="muted">No sources yet.</p>}
          {feeds.data?.items.map(feed => (
            <article key={feed.id} className="source-row" onClick={() => setSelected(feed)}>
              <div><strong>{feed.name}</strong><small>{feed.url}</small><small>{feed.fetching_mode.split('_').join(' ')}</small></div>
              <span className={`badge ${feed.last_success_at ? 'healthy' : 'pending'}`}>{feed.enabled ? (feed.last_success_at ? 'Healthy' : 'Awaiting poll') : 'Disabled'}</span>
              <div className="actions">
                <select value={feed.fetching_mode} aria-label="Collection mode" onClick={event => event.stopPropagation()} onChange={event => update.mutate({ feed, changes: { fetching_mode: event.target.value as Feed['fetching_mode'] } })}>
                  <option value="rss">RSS only</option>
                  <option value="full_text">Full text</option>
                  <option value="full_text_html">Full text + HTML</option>
                </select>
                <button className="secondary" onClick={event => { event.stopPropagation(); update.mutate({ feed, changes: { enabled: !feed.enabled } }) }}>{feed.enabled ? 'Disable' : 'Enable'}</button>
                <button className="secondary" onClick={event => { event.stopPropagation(); poll.mutate(feed.id) }}>Poll now</button>
                <button className="danger" onClick={event => { event.stopPropagation(); confirmRetire(feed) }}>Retire</button>
              </div>
            </article>
          ))}
          {feeds.data?.next_cursor && <button className="secondary" onClick={() => setCursor(feeds.data?.next_cursor ?? undefined)}>Next page</button>}
        </section>
      </div>
      {selected && (
        <section className="panel history">
          <h3>{selected.name} fetch history</h3>
          {!history.data?.items.length && <p className="muted">No fetch attempts.</p>}
          {history.data?.items.map(fetch => (
            <article key={fetch.id} className="history-row">
              <strong>{fetch.status}</strong>
              <span>{new Date(fetch.started_at).toLocaleString()}</span>
              <span>{fetch.new_article_count} new / {fetch.entry_count} entries</span>
              {fetch.error_message && <span className="error">{fetch.error_category}: {fetch.error_message}</span>}
            </article>
          ))}
        </section>
      )}
    </>
  )
}
