'use client'

import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { useId, useState } from 'react'
import { LoadError, LoadingState, Note } from '../../components/Feedback'
import { api, ApiError, type FeedListParams } from '../../lib/api'
import type { Feed } from '../../lib/api-types'
import { GlassPanel } from '../../components/GlassPanel'
import { PageHeader } from '../../components/PageHeader'
import { StatusBadge, type BadgeTone } from '../../components/StatusBadge'
import { chipClass, fieldClass, ghostButtonClass, labelClass, primaryButtonClass } from '../../lib/ui-classes'
import { sourceHref } from '../../lib/investigation'
import { cn } from '../../lib/utils'
import { CountrySelect, LanguageSelect } from '../../components/SearchablePicker'
import { countryLabel, languageLabel } from '../../lib/countries'

type Mode = 'rss' | 'full_text' | 'full_text_html'
type ListStatus = NonNullable<FeedListParams['status']>

const MODES: { value: Mode; label: string }[] = [
  { value: 'rss', label: 'RSS metadata only' },
  { value: 'full_text', label: 'Readable full text' },
  { value: 'full_text_html', label: 'Full text + retained HTML' },
]
const SHOW: { value: '' | ListStatus; label: string }[] = [
  { value: '', label: 'All sources' },
  { value: 'active', label: 'Collecting' },
  { value: 'failing', label: 'Failing' },
  { value: 'disabled', label: 'Disabled' },
  { value: 'retired', label: 'Retired' },
]

const message = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback
const splitTags = (value: string) => value.split(',').map(tag => tag.trim()).filter(Boolean)

function feedState(feed: Feed): { label: string; tone: BadgeTone } {
  if (feed.retired_at) return { label: 'Retired', tone: 'pending' }
  if (!feed.enabled) return { label: 'Disabled', tone: 'pending' }
  if (feed.last_fetch_status === 'failed') return { label: 'Failing', tone: 'error' }
  return feed.last_success_at ? { label: 'Healthy', tone: 'healthy' } : { label: 'Awaiting poll', tone: 'pending' }
}

function feedSummary(feed: Feed): string {
  return [
    feed.source_country && countryLabel(feed.source_country),
    feed.expected_language && languageLabel(feed.expected_language),
    feed.fetching_mode.split('_').join(' '),
    `every ${feed.poll_interval_minutes} min`,
  ].filter(Boolean).join(' · ')
}

/** The source's settings as a form; Save sends only the fields that changed. */
function EditSource({ feed, onDone }: { feed: Feed; onDone: () => void }) {
  const client = useQueryClient()
  const [name, setName] = useState(feed.name)
  const [url, setUrl] = useState(feed.url)
  const [country, setCountry] = useState(feed.source_country ?? '')
  const [language, setLanguage] = useState(feed.expected_language ?? '')
  const [tags, setTags] = useState(feed.tags.join(', '))
  const [mode, setMode] = useState<Mode>(feed.fetching_mode)
  const [interval, setInterval] = useState(String(feed.poll_interval_minutes))
  const [enabled, setEnabled] = useState(feed.enabled)
  const refresh = () => client.invalidateQueries({ queryKey: ['feeds'] })
  const save = useMutation({ mutationFn: (changes: Partial<Feed>) => api.updateFeed(feed.id, changes), onSuccess: () => { refresh(); onDone() } })
  const retire = useMutation({ mutationFn: () => api.retireFeed(feed.id), onSuccess: () => { refresh(); onDone() } })

  function changes(): Partial<Feed> {
    const next: Partial<Feed> = {}
    if (name.trim() !== feed.name) next.name = name.trim()
    if (url.trim() !== feed.url) next.url = url.trim()
    if ((country || null) !== feed.source_country) next.source_country = country || null
    if ((language || null) !== feed.expected_language) next.expected_language = language || null
    const tagList = splitTags(tags)
    if (tagList.join('\n') !== feed.tags.join('\n')) next.tags = tagList
    if (mode !== feed.fetching_mode) next.fetching_mode = mode
    if (Number(interval) !== feed.poll_interval_minutes) next.poll_interval_minutes = Number(interval)
    if (enabled !== feed.enabled) next.enabled = enabled
    return next
  }
  function submit() {
    const next = changes()
    if (Object.keys(next).length) save.mutate(next)
    else onDone()
  }
  function confirmRetire() { if (window.confirm('Retire this source? Its archive will be preserved.')) retire.mutate() }
  const error = save.error ?? retire.error

  return (
    <form aria-label={`Edit ${feed.name}`} className="grid grid-cols-1 gap-4 rounded-lg border border-border bg-background/40 p-4 sm:grid-cols-2" onSubmit={event => { event.preventDefault(); submit() }}>
      <label className={labelClass}>Name<input className={cn(fieldClass, 'mt-1')} value={name} onChange={event => setName(event.target.value)} required maxLength={200} /></label>
      <label className={labelClass}>Feed URL<input className={cn(fieldClass, 'mt-1')} value={url} onChange={event => setUrl(event.target.value)} required type="url" /></label>
      <CountrySelect label="Source country" value={country} emptyLabel="Not set" onChange={setCountry} />
      <LanguageSelect label="Expected language" value={language} emptyLabel="Not set" onChange={setLanguage} />
      <div className="flex flex-col gap-1 sm:col-span-2">
        <label className={labelClass}>Tags<input className={cn(fieldClass, 'mt-1')} value={tags} onChange={event => setTags(event.target.value)} placeholder="greek, economy" aria-describedby={`tags-help-${feed.id}`} /></label>
        <p id={`tags-help-${feed.id}`} className="text-[11px] text-muted-foreground">Separate tags with commas.</p>
      </div>
      <label className={labelClass}>Collection mode
        <select className={cn(fieldClass, 'mt-1')} value={mode} onChange={event => setMode(event.target.value as Mode)}>
          {MODES.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
      </label>
      <label className={labelClass}>Poll interval (minutes)<input className={cn(fieldClass, 'mt-1')} value={interval} onChange={event => setInterval(event.target.value)} type="number" min={5} max={10080} required /></label>
      <label className="flex items-center gap-2 text-sm text-foreground sm:col-span-2">
        <input type="checkbox" className="h-4 w-4 accent-primary" checked={enabled} onChange={event => setEnabled(event.target.checked)} />Collect from this source
      </label>
      {error && <p role="alert" className="error text-sm text-destructive sm:col-span-2">{message(error, 'Could not change this source.')}</p>}
      <div className="flex flex-wrap items-center gap-2 sm:col-span-2">
        <button className={primaryButtonClass} disabled={save.isPending}>{save.isPending ? 'Saving…' : 'Save changes'}</button>
        <button type="button" className={ghostButtonClass} onClick={onDone}>Cancel</button>
        <button type="button" className={cn(ghostButtonClass, 'ml-auto border-destructive/30 text-destructive hover:border-destructive hover:text-destructive')} disabled={retire.isPending} onClick={confirmRetire}>Retire source</button>
      </div>
    </form>
  )
}

export default function SourcesPage() {
  const client = useQueryClient()
  // Cursors of the pages before the one shown, so Previous goes back without refetching from the start.
  const [cursors, setCursors] = useState<(string | undefined)[]>([undefined])
  const [filters, setFilters] = useState<FeedListParams>({})
  const [editing, setEditing] = useState<string>()
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [country, setCountry] = useState('')
  const [language, setLanguage] = useState('')
  const [pollIntervalMinutes, setPollIntervalMinutes] = useState(60)
  const [mode, setMode] = useState<Mode>('rss')

  // The filter lists are labelled by id: a wrapping label would read every option ("Name") as its text.
  const listId = useId()
  const cursor = cursors[cursors.length - 1]
  const params = Object.fromEntries(Object.entries(filters).filter(([, value]) => value)) as FeedListParams
  const feeds = useQuery({ queryKey: ['feeds', cursor, params], queryFn: () => api.feeds(cursor, params), placeholderData: keepPreviousData })
  const tags = useQuery({ queryKey: ['feeds', 'tags'], queryFn: api.feedTags })
  const save = useMutation({
    mutationFn: () => api.createFeed({ name, url, source_country: country || undefined, expected_language: language || undefined, poll_interval_minutes: pollIntervalMinutes, fetching_mode: mode }),
    onSuccess: () => { setName(''); setUrl(''); client.invalidateQueries({ queryKey: ['feeds'] }) },
  })
  const poll = useMutation({ mutationFn: api.pollFeed, onSuccess: () => client.invalidateQueries({ queryKey: ['feeds'] }) })

  function filter(changes: FeedListParams) { setFilters(current => ({ ...current, ...changes })); setCursors([undefined]) }
  const filtered = Object.keys(params).some(key => key !== 'sort')

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="Collection" title="Sources" />
      <p className="-mt-2 text-sm text-muted-foreground">Choose RSS metadata, readable full text, or full text with retained source HTML for each source.</p>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
        <GlassPanel className="flex flex-col gap-4 self-start">
          <h3 className="text-sm font-semibold text-foreground">Add source</h3>
          <form className="flex flex-col gap-4" onSubmit={event => { event.preventDefault(); save.mutate() }}>
            <label className={labelClass}>Name<input className={cn(fieldClass, 'mt-1')} value={name} onChange={event => setName(event.target.value)} required maxLength={200} /></label>
            <label className={labelClass}>Feed URL<input className={cn(fieldClass, 'mt-1')} value={url} onChange={event => setUrl(event.target.value)} required type="url" /></label>
            <div className="grid grid-cols-2 gap-4">
              <CountrySelect label="Source country" value={country} emptyLabel="Not set" onChange={setCountry} />
              <LanguageSelect label="Expected language" value={language} emptyLabel="Not set" onChange={setLanguage} />
            </div>
            <label className={labelClass}>Collection mode
              <select className={cn(fieldClass, 'mt-1')} value={mode} onChange={event => setMode(event.target.value as Mode)}>
                {MODES.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </label>
            <label className={labelClass}>Poll interval (minutes)<input className={cn(fieldClass, 'mt-1')} value={pollIntervalMinutes} onChange={event => setPollIntervalMinutes(Number(event.target.value))} type="number" min={5} max={10080} /></label>
            <button className={cn(primaryButtonClass, 'self-start')} disabled={save.isPending}>{save.isPending ? 'Adding…' : 'Add source'}</button>
            {save.isError && <p role="alert" className="error text-sm text-destructive">{message(save.error, 'Could not add this source.')}</p>}
            {save.isSuccess && <p role="status" className="text-sm text-primary">Source added. Its first poll is scheduled now; articles appear under Articles once it has run.</p>}
          </form>
        </GlassPanel>

        <GlassPanel className="overflow-hidden p-0">
          <h3 className="px-6 pt-6 text-sm font-semibold text-foreground">Managed sources</h3>
          <div className="grid grid-cols-1 gap-3 px-6 pt-4 sm:grid-cols-2 xl:grid-cols-4" role="search" aria-label="Find sources">
            <label className={labelClass}>Find a source<input className={cn(fieldClass, 'mt-1')} value={filters.q ?? ''} onChange={event => filter({ q: event.target.value })} placeholder="Name or URL" /></label>
            <div className={labelClass}>
              <label htmlFor={`${listId}-show`}>Show</label>
              <select id={`${listId}-show`} className={fieldClass} value={filters.status ?? ''} onChange={event => filter({ status: (event.target.value || undefined) as ListStatus | undefined })}>
                {SHOW.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </div>
            <div className={labelClass}>
              <label htmlFor={`${listId}-tag`}>Tag</label>
              <select id={`${listId}-tag`} className={fieldClass} value={filters.tag ?? ''} onChange={event => filter({ tag: event.target.value || undefined })}>
                <option value="">Any tag</option>
                {filters.tag && !tags.data?.some(item => item.tag === filters.tag) && <option value={filters.tag}>{filters.tag}</option>}
                {tags.data?.map(item => <option key={item.tag} value={item.tag}>{`${item.tag} (${item.count})`}</option>)}
              </select>
            </div>
            <div className={labelClass}>
              <label htmlFor={`${listId}-sort`}>Sort</label>
              <select id={`${listId}-sort`} className={fieldClass} value={filters.sort ?? 'newest'} onChange={event => filter({ sort: event.target.value === 'name' ? 'name' : undefined })}>
                <option value="newest">Newest first</option>
                <option value="name">Name</option>
              </select>
            </div>
          </div>
          {feeds.isPending && <LoadingState label="Loading sources…" />}
          {feeds.isError && <LoadError className="px-6 py-4" query={feeds} message="Could not load sources." />}
          {feeds.isSuccess && !feeds.data.items.length && (filtered
            ? <Note>No sources match these filters.</Note>
            : <Note>No sources yet. Add an RSS or Atom feed with the form to start collecting.</Note>)}
          {poll.isError && <p role="alert" className="error px-6 py-4 text-sm text-destructive">{message(poll.error, 'Could not poll this source.')}</p>}
          <div className="mt-4 flex flex-col">
            {feeds.data?.items.map(feed => {
              const state = feedState(feed)
              return (
                <article key={feed.id} className="source-row flex flex-col gap-2 border-t border-border px-6 py-3">
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex min-w-0 flex-col gap-0.5">
                      <strong className="text-[15px] font-semibold text-foreground">{feed.name}</strong>
                      <small className="break-all text-xs text-muted-foreground">{feed.url}</small>
                      <small className="text-xs text-muted-foreground">{feedSummary(feed)}</small>
                    </div>
                    <StatusBadge tone={state.tone}>{state.label}</StatusBadge>
                  </div>
                  {feed.last_fetch_error && !feed.retired_at && <p className="error text-xs text-destructive">{`Last fetch failed: ${feed.last_fetch_error}`}</p>}
                  {(feed.tags.length > 0 || !feed.retired_at) && (
                    <div className="flex flex-wrap items-center gap-2">
                      {feed.tags.map(tag => feed.retired_at
                        ? <span key={tag} className={chipClass}>{tag}</span>
                        : <button key={tag} type="button" className={chipClass} aria-label={`Filter by tag ${tag}`} onClick={() => filter({ tag })}>{tag}</button>)}
                      {!feed.retired_at && (
                        <span className="ml-auto flex flex-wrap gap-2">
                          <button type="button" className={ghostButtonClass} aria-label={`Edit ${feed.name}`} aria-expanded={editing === feed.id} onClick={() => setEditing(editing === feed.id ? undefined : feed.id)}>Edit</button>
                          <button type="button" className={ghostButtonClass} disabled={!feed.enabled} onClick={() => poll.mutate(feed.id)}>Poll now</button>
                          <Link className={ghostButtonClass} aria-label={`Dossier for ${feed.name}`} href={sourceHref(feed.id)}>Dossier</Link>
                        </span>
                      )}
                    </div>
                  )}
                  {editing === feed.id && <EditSource feed={feed} onDone={() => setEditing(undefined)} />}
                </article>
              )
            })}
          </div>
          {(cursors.length > 1 || feeds.data?.next_cursor) && (
            <div className="flex gap-2 border-t border-border px-6 py-4">
              {cursors.length > 1 && <button type="button" className={ghostButtonClass} onClick={() => setCursors(current => current.slice(0, -1))}>Previous page</button>}
              {feeds.data?.next_cursor && <button type="button" className={ghostButtonClass} onClick={() => setCursors(current => [...current, feeds.data?.next_cursor ?? undefined])}>Next page</button>}
            </div>
          )}
        </GlassPanel>
      </div>
    </div>
  )
}
