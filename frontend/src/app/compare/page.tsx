'use client'

import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useState } from 'react'
import { ApiError, api, type CompareSpec } from '../../lib/api'
import type { CompareKind, ComparePart, CompareResponse, CompareRole } from '../../lib/api-types'
import { BarChart } from '../../components/BarChart'
import { GlassPanel } from '../../components/GlassPanel'
import { PageHeader } from '../../components/PageHeader'
import { plural } from '../../lib/utils'
import { chipClass, fieldClass, ghostButtonClass, labelClass } from '../../lib/ui-classes'
import { clusterHref, compareHref, emptyInvestigation, entityHref, queryFromState, refine, sourceHref, toHref, type ListField } from '../../lib/investigation'

const KINDS: { value: CompareKind; label: string }[] = [{ value: 'entity', label: 'Entities' }, { value: 'source', label: 'Sources' }, { value: 'country', label: 'Countries' }]
const ROLES: { value: CompareRole; label: string }[] = [{ value: 'story', label: 'Story country' }, { value: 'mentioned', label: 'Mentioned country' }, { value: 'source', label: 'Source country' }]
const WINDOWS = [7, 30, 90, 365]
const COUNTRY = /^[A-Za-z]{2}$/
const DAY_MS = 86_400_000
const nextDay = (day: string) => new Date(Date.parse(day) + DAY_MS).toISOString().slice(0, 10)
const when = (value: string | null | undefined) => value ? new Date(value).toLocaleString() : '—'
const pageOf = <T extends { next_cursor: string | null }>(load: (cursor?: string) => Promise<T>) => ({
  initialPageParam: undefined as string | undefined,
  queryFn: ({ pageParam }: { pageParam: string | undefined }) => load(pageParam),
  getNextPageParam: (page: T) => page.next_cursor ?? undefined,
  retry: false,
})
// The annotation role `primary` is what the rest of the app calls the story country.
const roleName = (role: string) => role === 'primary' ? 'story' : role
const searchField = (kind: CompareKind, role?: CompareRole): ListField => kind === 'entity' ? 'entity_id' : kind === 'source' ? 'source_id' : role === 'mentioned' ? 'mentioned_country' : role === 'source' ? 'source_country' : 'story_country'

function Note({ children, error }: { children: string; error?: boolean }) {
  return <p className={error ? 'error px-6 py-4 text-sm text-destructive' : 'px-6 py-4 text-sm text-muted-foreground'}>{children}</p>
}

interface PickerProps { kind: CompareKind; slot: 'A' | 'B'; value: string; label: string; onPick(ref: string, label?: string): void }

function SubjectPicker({ kind, slot, value, label, onPick }: PickerProps) {
  const [text, setText] = useState('')
  const lookup = useQuery({ queryKey: ['compare-entities', text.trim()], queryFn: () => api.nlpEntities(text.trim()), enabled: kind === 'entity' && text.trim().length >= 2, retry: false })
  const feeds = useInfiniteQuery({ queryKey: ['compare-feeds'], ...pageOf(cursor => api.feeds(cursor)), enabled: kind === 'source' })
  const options = feeds.data?.pages.flatMap(page => page.items) ?? []

  if (kind === 'source') {
    return (
      <div className="flex flex-col gap-2">
        <label className={labelClass}>{`Source ${slot}`}
          <select className={fieldClass} value={value} onChange={event => onPick(event.target.value, options.find(feed => feed.id === event.target.value)?.name)}>
            <option value="">Choose a source</option>
            {value && !options.some(feed => feed.id === value) && <option value={value}>{label}</option>}
            {options.map(feed => <option key={feed.id} value={feed.id}>{feed.name}</option>)}
          </select>
        </label>
        {feeds.hasNextPage && <button className={ghostButtonClass} disabled={feeds.isFetchingNextPage} onClick={() => feeds.fetchNextPage()}>Load more sources</button>}
      </div>
    )
  }
  if (kind === 'country') {
    return (
      <label className={labelClass}>{`Country ${slot}`}
        <input className={fieldClass} maxLength={2} placeholder="Two-letter code, e.g. GR" defaultValue={value}
          onChange={event => { const code = event.target.value.trim(); if (COUNTRY.test(code)) onPick(code.toUpperCase()); else if (!code) onPick('') }} />
      </label>
    )
  }
  return (
    <div className="flex flex-col gap-2">
      <label className={labelClass}>{`Entity ${slot}`}
        <input className={fieldClass} placeholder="Search entities" value={text} onChange={event => setText(event.target.value)} />
      </label>
      {value && <p className="text-sm text-foreground">{`Selected: ${label}`}</p>}
      {lookup.isError && <p className="error text-sm text-destructive">Could not search entities.</p>}
      {lookup.data && !lookup.data.items.length && <p className="text-sm text-muted-foreground">No entities match.</p>}
      <div className="flex flex-wrap gap-2">
        {lookup.data?.items.map(item => <button key={item.id} className={chipClass} onClick={() => { onPick(item.id, item.text); setText('') }}>{`${item.text} (${item.kind})`}</button>)}
      </div>
    </div>
  )
}

function OverlapSection({ title, noun, nouns, overlap, aLabel, bLabel, onShow }: { title: string; noun: string; nouns: string; overlap: CompareResponse['overlap']['articles']; aLabel: string; bLabel: string; onShow?: (part: ComparePart) => void }) {
  const parts: [ComparePart, string, number][] = [['a', `Only ${aLabel}`, overlap.only_a], ['both', 'Both', overlap.both], ['b', `Only ${bLabel}`, overlap.only_b]]
  return (
    <section aria-label={`${title} overlap`} className="flex flex-col gap-1.5">
      <h4 className="text-sm font-semibold text-foreground">{title}</h4>
      <p className="text-sm text-foreground">
        {overlap.jaccard === null ? `No ${nouns} in either set.` : `${overlap.both} of ${plural(overlap.union, noun, nouns)} in either set appear in both · Jaccard ${overlap.jaccard.toFixed(2)} (${overlap.both} ÷ ${overlap.union})`}
      </p>
      <div className="flex flex-wrap gap-2">
        {parts.map(([part, name, count]) => onShow
          ? <button key={part} className={chipClass} onClick={() => onShow(part)}>{`${name}: ${count}`}</button>
          : <span key={part} className="text-xs text-muted-foreground">{`${name}: ${count}`}</span>)}
      </div>
    </section>
  )
}

function Related({ title, aLabel, bLabel, rows }: { title: string; aLabel: string; bLabel: string; rows: { key: string; label: React.ReactNode; a: number; b: number }[] }) {
  return (
    <div className="flex flex-col gap-1">
      <h4 className="text-sm font-semibold text-foreground">{title}</h4>
      {!rows.length ? <p className="text-sm text-muted-foreground">None.</p> : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead><tr className="text-xs text-muted-foreground"><th className="py-1 font-medium">{title}</th><th className="py-1 font-medium">{`Articles with ${aLabel}`}</th><th className="py-1 font-medium">{`Articles with ${bLabel}`}</th></tr></thead>
            <tbody>{rows.map(row => <tr key={row.key} className="border-t border-border"><td className="py-1 pr-3 text-foreground">{row.label}</td><td className="py-1 pr-3 text-foreground">{row.a}</td><td className="py-1 text-foreground">{row.b}</td></tr>)}</tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function CompareContent() {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const currentHref = toHref(pathname, searchParams)
  const kind = KINDS.find(item => item.value === searchParams.get('kind'))?.value ?? 'entity'
  const a = searchParams.get('a') ?? ''
  const b = searchParams.get('b') ?? ''
  const role = kind === 'country' ? ROLES.find(item => item.value === searchParams.get('role'))?.value ?? 'story' : undefined
  const days = WINDOWS.includes(Number(searchParams.get('days'))) ? Number(searchParams.get('days')) : 30
  const [picked, setPicked] = useState<Record<string, string>>({})
  const [tab, setTab] = useState<'articles' | 'stories'>('articles')
  const [part, setPart] = useState<ComparePart>('both')
  // A country input is uncontrolled (a half-typed code is not in the URL), so a swap remounts the pickers.
  const [swaps, setSwaps] = useState(0)

  const same = Boolean(a && b) && (kind === 'country' ? a.toUpperCase() === b.toUpperCase() : a === b)
  const valid = (ref: string) => kind !== 'country' || COUNTRY.test(ref)
  const ready = Boolean(a && b) && !same && valid(a) && valid(b)
  const spec: CompareSpec = { kind, a, b, ...(role && { role }), days }
  const compare = useQuery({ queryKey: ['compare', spec], queryFn: () => api.compare(spec), enabled: ready, retry: false })
  const articles = useInfiniteQuery({ queryKey: ['compare-articles', spec, part], ...pageOf(cursor => api.compareArticles(spec, part, cursor)), enabled: compare.isSuccess && tab === 'articles' })
  const stories = useInfiniteQuery({ queryKey: ['compare-stories', spec, part], ...pageOf(cursor => api.compareStories(spec, part, cursor)), enabled: compare.isSuccess && tab === 'stories' })

  const data = compare.data
  const nameOf = (ref: string, side?: 'a' | 'b') => picked[ref] ?? (side && data ? data[side].subject.label : ref)
  const aLabel = nameOf(a, 'a')
  const bLabel = nameOf(b, 'b')
  const articleItems = articles.data?.pages.flatMap(page => page.items) ?? []
  const storyItems = stories.data?.pages.flatMap(page => page.items) ?? []
  const openArticle = (id: string) => toHref('/articles', new URLSearchParams({ article: id, from: currentHref }))

  function go(next: { kind?: CompareKind; a?: string; b?: string; role?: CompareRole; days?: number }) {
    router.replace(compareHref({ kind, a, b, role, days, ...next }))
  }
  function pick(slot: 'a' | 'b', ref: string, label?: string) {
    if (label) setPicked(current => ({ ...current, [ref]: label }))
    go({ [slot]: ref })
  }
  const searchDay = (ref: string, day: string) => toHref('/search', queryFromState({ ...refine(emptyInvestigation(), searchField(kind, role), ref), after: day, before: nextDay(day) }))
  function show(nextTab: 'articles' | 'stories', nextPart: ComparePart) { setTab(nextTab); setPart(nextPart) }

  const many = KINDS.find(item => item.value === kind)!.label.toLowerCase()
  const problem = !a && !b ? `Choose two ${many} to compare.`
    : !a || !b ? `Choose a second ${kind}.`
    : same ? `Choose two different ${many}.`
    : !valid(a) || !valid(b) ? 'A country is a two-letter code.' : ''

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="Compare" title="Compare">
        <label className={labelClass}>Window
          <select className={fieldClass} value={days} onChange={event => go({ days: Number(event.target.value) })}>
            {WINDOWS.map(option => <option key={option} value={option}>Last {option} days</option>)}
          </select>
        </label>
      </PageHeader>

      <GlassPanel className="flex flex-col gap-4">
        <div className="flex flex-wrap items-end gap-4">
          <label className={labelClass}>Compare
            <select className={fieldClass} value={kind} onChange={event => router.replace(compareHref({ kind: event.target.value as CompareKind, days }))}>
              {KINDS.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
          </label>
          {kind === 'country' && (
            <label className={labelClass}>Country meaning
              <select className={fieldClass} value={role} onChange={event => go({ role: event.target.value as CompareRole })}>
                {ROLES.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </label>
          )}
          <button className={ghostButtonClass} disabled={!a && !b} onClick={() => { setSwaps(n => n + 1); go({ a: b, b: a }) }}>Swap</button>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <SubjectPicker key={`${kind}-a-${swaps}`} kind={kind} slot="A" value={a} label={aLabel} onPick={(ref, label) => pick('a', ref, label)} />
          <SubjectPicker key={`${kind}-b-${swaps}`} kind={kind} slot="B" value={b} label={bLabel} onPick={(ref, label) => pick('b', ref, label)} />
        </div>
      </GlassPanel>

      {problem && <GlassPanel className="p-0"><Note>{problem}</Note></GlassPanel>}
      {ready && compare.isPending && <GlassPanel className="p-0"><Note>Loading comparison…</Note></GlassPanel>}
      {compare.isError && <GlassPanel className="p-0"><Note error>{compare.error instanceof ApiError && compare.error.status === 404 ? 'One of these subjects was not found.' : 'Could not load this comparison.'}</Note></GlassPanel>}

      {data && (
        <>
          <div className="grid gap-6 md:grid-cols-2">
            {(['a', 'b'] as const).map(slot => {
              const side = data[slot]
              return (
                <GlassPanel key={slot} className="flex flex-col gap-2">
                  <h3 className="text-lg font-semibold text-foreground">{side.subject.label}</h3>
                  <p className="text-sm text-muted-foreground">{`${slot.toUpperCase()} · ${side.subject.kind}${side.subject.role ? ` · ${side.subject.role} country` : ''}`}</p>
                  {side.subject.retired && <p className="text-sm text-muted-foreground">Retired. Its archive is kept.</p>}
                  <p className="text-sm text-foreground">{`Last ${data.window_days} days: ${plural(side.articles, 'article')} · ${plural(side.stories, 'story', 'stories')}${side.sources === null ? '' : ` · ${plural(side.sources, 'source')}`}`}</p>
                  {side.subject.kind !== 'country' && <Link className={chipClass} href={side.subject.kind === 'entity' ? entityHref(side.subject.ref) : sourceHref(side.subject.ref, currentHref)}>Open dossier</Link>}
                </GlassPanel>
              )
            })}
          </div>

          <GlassPanel className="flex flex-col gap-4">
            <h3 className="text-sm font-semibold text-foreground">Overlap</h3>
            {data.overlap.articles.union === 0 && <p className="text-sm text-muted-foreground">Neither has articles in this window.</p>}
            <OverlapSection title="Articles" noun="article" nouns="articles" overlap={data.overlap.articles} aLabel={aLabel} bLabel={bLabel} onShow={next => show('articles', next)} />
            <OverlapSection title="Stories" noun="story" nouns="stories" overlap={data.overlap.stories} aLabel={aLabel} bLabel={bLabel} onShow={next => show('stories', next)} />
            {data.overlap.sources && <OverlapSection title="Sources" noun="source" nouns="sources" overlap={data.overlap.sources} aLabel={aLabel} bLabel={bLabel} />}
            <p className="text-xs text-muted-foreground">Jaccard is the shared count divided by the count in either set. It describes overlap only, and says nothing about which subject matters more.</p>
          </GlassPanel>

          <div className="grid gap-6 lg:grid-cols-2">
            {(['a', 'b'] as const).map(slot => (
              <GlassPanel key={slot} className="flex flex-col gap-2">
                <h3 className="text-sm font-semibold text-foreground">{`Articles per day (UTC): ${data[slot].subject.label}`}</h3>
                <BarChart items={data[slot].timeline.map(day => ({ id: day.date, label: day.date, value: day.article_count }))} valueLabel="Articles"
                  ariaLabel={`${data[slot].subject.label}: articles per day. Click a bar to search that day's articles.`}
                  onSelect={item => router.push(searchDay(data[slot].subject.ref, item.id))} />
              </GlassPanel>
            ))}
          </div>

          <GlassPanel className="flex flex-col gap-4">
            <h3 className="text-sm font-semibold text-foreground">Related to either</h3>
            <p className="text-xs text-muted-foreground">Counts are articles in the window that mention each item, per subject, ordered by the combined count. A zero means the item does not appear with that subject.</p>
            <Related title="Entities" aLabel={aLabel} bLabel={bLabel}
              rows={data.related.entities.map(item => ({ key: item.id, label: <Link className="hover:underline" href={entityHref(item.id)}>{item.label}</Link>, a: item.a_articles, b: item.b_articles }))} />
            <Related title="Countries" aLabel={aLabel} bLabel={bLabel}
              rows={data.related.countries.map(item => ({ key: `${item.role}-${item.country_code}`, label: `${item.country_code} · ${roleName(item.role)}`, a: item.a_articles, b: item.b_articles }))} />
            {data.related.sources && <Related title="Sources" aLabel={aLabel} bLabel={bLabel}
              rows={data.related.sources.map(item => ({ key: item.id, label: <Link className="hover:underline" href={sourceHref(item.id, currentHref)}>{item.label}</Link>, a: item.a_articles, b: item.b_articles }))} />}
          </GlassPanel>

          <GlassPanel className="overflow-hidden p-0">
            <div className="flex flex-wrap items-end gap-4 px-6 pt-4">
              <h3 className="text-sm font-semibold text-foreground">Evidence</h3>
              <div className="flex gap-2">
                <button className={chipClass} aria-pressed={tab === 'articles'} onClick={() => setTab('articles')}>Articles</button>
                <button className={chipClass} aria-pressed={tab === 'stories'} onClick={() => setTab('stories')}>Stories</button>
              </div>
              <label className={labelClass}>Show
                <select className={fieldClass} value={part} onChange={event => setPart(event.target.value as ComparePart)}>
                  <option value="a">{`Only ${aLabel}`}</option>
                  <option value="both">Both</option>
                  <option value="b">{`Only ${bLabel}`}</option>
                </select>
              </label>
            </div>
            {tab === 'articles' && (
              <>
                {articles.isPending && <Note>Loading articles…</Note>}
                {articles.isError && <Note error>Could not load articles.</Note>}
                {articles.data && !articleItems.length && <Note>No articles in this part.</Note>}
                {articleItems.map(article => (
                  <div key={article.id} className="flex flex-col gap-0.5 border-border px-6 py-3">
                    <Link className="text-[15px] font-semibold text-foreground hover:underline" href={openArticle(article.id)}>{article.title}</Link>
                    <span className="text-xs text-muted-foreground">{when(article.published_at ?? article.first_discovered_at)}</span>
                  </div>
                ))}
                {articles.hasNextPage && <div className="px-6 py-4"><button className={ghostButtonClass} disabled={articles.isFetchingNextPage} onClick={() => articles.fetchNextPage()}>{articles.isFetchingNextPage ? 'Loading…' : 'Load more articles'}</button></div>}
              </>
            )}
            {tab === 'stories' && (
              <>
                {stories.isPending && <Note>Loading stories…</Note>}
                {stories.isError && <Note error>Could not load stories.</Note>}
                {stories.data && !storyItems.length && <Note>No stories in this part.</Note>}
                {storyItems.map(story => (
                  <div key={story.id} className="flex flex-col gap-0.5 border-border px-6 py-3">
                    <Link className="text-[15px] font-semibold text-foreground hover:underline" href={clusterHref(story.id, currentHref)}>{story.representative_article?.title ?? 'Story'}</Link>
                    <span className="text-xs text-muted-foreground">{`${plural(story.article_count, 'article')} · ${plural(story.source_count, 'source')} · ${story.a_articles} from ${aLabel} · ${story.b_articles} from ${bLabel}`}</span>
                  </div>
                ))}
                {stories.hasNextPage && <div className="px-6 py-4"><button className={ghostButtonClass} disabled={stories.isFetchingNextPage} onClick={() => stories.fetchNextPage()}>{stories.isFetchingNextPage ? 'Loading…' : 'Load more stories'}</button></div>}
              </>
            )}
          </GlassPanel>
        </>
      )}
    </div>
  )
}

export default function ComparePage() {
  return <Suspense fallback={null}><CompareContent /></Suspense>
}
