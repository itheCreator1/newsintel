import { useMutation, type UseQueryResult } from '@tanstack/react-query'
import Link from 'next/link'
import { useState } from 'react'
import { api } from '../../lib/api'
import type { OpsFeed, OpsFeeds } from '../../lib/api-types'
import { sourceHref } from '../../lib/investigation'
import { feedTone, since, span } from '../../lib/operations'
import type { FeedView } from '../../lib/processes'
import { ghostButtonClass } from '../../lib/ui-classes'
import { cn, plural } from '../../lib/utils'
import { GlassPanel } from '../GlassPanel'
import { LoadError, Note } from '../Feedback'
import { StatusBadge } from '../StatusBadge'
import { AsOf, failure, when } from './parts'

const STATES = { ok: 'OK', overdue: 'Overdue', failing: 'Failing', awaiting: 'Awaiting first fetch', disabled: 'Disabled' } as const
const chipClass = 'w-auto rounded-full border border-border bg-card px-3 py-1.5 text-xs font-semibold text-muted-foreground transition-colors hover:border-ring aria-pressed:border-accent aria-pressed:bg-accent aria-pressed:text-accent-foreground'
const needsAttention = (feed: OpsFeed) => feed.state === 'failing' || feed.state === 'overdue'
const hoursText = (hours: number) => hours === 1 ? 'hour' : hours === 168 ? '7 days' : `${hours} hours`

function FeedItem({ feed, generatedAt }: { feed: OpsFeed; generatedAt: string }) {
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null)
  const poll = useMutation({
    mutationFn: () => api.pollFeed(feed.id),
    onSuccess: result => setMessage({ text: result.reused ? 'A fetch is already running.' : 'Fetch queued.' }),
    onError: error => setMessage({ text: failure(error, 'Could not queue the fetch.'), error: true }),
  })
  const streak = feed.failure_streak
  const categories = Object.entries(feed.failures_by_category)
  return (
    <li className="flex flex-col gap-1 border-t border-border px-6 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <Link className="text-[15px] font-semibold text-foreground no-underline hover:underline" href={sourceHref(feed.id)}>{feed.name}</Link>
        <StatusBadge tone={feedTone(feed.state)}>{STATES[feed.state]}</StatusBadge>
        {feed.state === 'overdue' && <span className="text-xs text-muted-foreground">Overdue by {span(feed.overdue_seconds)}</span>}
        {streak > 0 && <span className="text-xs text-destructive">{feed.streak_capped ? `${streak}+` : streak} failed {streak === 1 ? 'fetch' : 'fetches'} in a row</span>}
        {feed.enabled && <button type="button" className={cn(ghostButtonClass, 'ml-auto')} disabled={poll.isPending} onClick={() => poll.mutate()}>Fetch now</button>}
      </div>
      <p className="text-xs text-muted-foreground">
        Last fetch {feed.last_fetch_at ? `${since(feed.last_fetch_at, generatedAt)} ago (${feed.last_fetch_status})` : 'never'} · last success {feed.last_success_at ? `${since(feed.last_success_at, generatedAt)} ago` : 'never'} · next poll {when(feed.next_poll_at)} · every {feed.poll_interval_minutes} min
      </p>
      {categories.length > 0 && <p className="text-xs text-muted-foreground">{categories.map(([name, total]) => `${name} ${total}`).join(' · ')}</p>}
      {message && <p role="status" className={cn('text-xs', message.error ? 'error text-destructive' : 'text-primary')}>{message.text}</p>}
    </li>
  )
}

/** Every feed with its fetch state; "Needs attention" keeps the failing and overdue ones. */
export function FeedsPanel({ feeds, hours, view, onView }: { feeds: UseQueryResult<OpsFeeds>; hours: number; view: FeedView; onView: (view: FeedView) => void }) {
  const items = feeds.data?.items ?? []
  const attention = items.filter(needsAttention)
  const shown = view === 'attention' ? attention : items
  return (
    <GlassPanel aria-label="Feeds" className="flex flex-col gap-3 p-0">
      <div className="flex flex-wrap items-end justify-between gap-3 px-6 pt-6">
        <h3 className="text-lg font-semibold text-foreground">Feeds</h3>
        <div className="flex flex-wrap items-center gap-3">
          {feeds.data && <AsOf at={feeds.data.generated_at} />}
          <div role="group" aria-label="Feed filter" className="flex gap-1.5">
            <button type="button" className={chipClass} aria-pressed={view === 'all'} aria-label={`All ${items.length}`} onClick={() => onView('all')}>All<span className="ml-1 font-mono">{items.length}</span></button>
            <button type="button" className={chipClass} aria-pressed={view === 'attention'} aria-label={`Needs attention ${attention.length}`} onClick={() => onView('attention')}>Needs attention<span className="ml-1 font-mono">{attention.length}</span></button>
          </div>
        </div>
      </div>
      <p className="px-6 text-xs text-muted-foreground">Failing: the newest finished fetches failed (counted over the last 20). Overdue: the next poll is more than one interval late. Awaiting: no fetch has finished yet.</p>
      {feeds.isPending && <Note>Loading the feeds…</Note>}
      {feeds.isError && <LoadError className="px-6 py-4" query={feeds} message="Could not load the feeds." />}
      {feeds.data && (
        <>
          <p className="px-6 text-sm text-foreground">
            In the last {hoursText(hours)}, {plural(feeds.data.totals.fetches, 'finished fetch', 'finished fetches')} returned {plural(feeds.data.totals.entries, 'entry', 'entries')}: {feeds.data.totals.invalid} invalid, {feeds.data.totals.new} new, {plural(feeds.data.totals.duplicates, 'duplicate')} (entries − invalid − new: already stored).
          </p>
          {feeds.data.truncated && <p className="px-6 text-xs text-muted-foreground">Showing the first 500 feeds.</p>}
          {items.length === 0 && <Note>No feeds yet.</Note>}
          {items.length > 0 && shown.length === 0 && <Note>No feed needs attention.</Note>}
          <ul className="m-0 list-none p-0 pb-2">{shown.map(item => <FeedItem key={item.id} feed={item} generatedAt={feeds.data.generated_at} />)}</ul>
        </>
      )}
    </GlassPanel>
  )
}
