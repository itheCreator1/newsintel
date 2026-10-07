'use client'

import { useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import type { ReactNode } from 'react'
import { api } from '../lib/api'
import { chipClass } from '../lib/ui-classes'
import { GlassPanel } from './GlassPanel'

// Polls while something is still missing, so the list ticks over as the first collection lands.
const POLL_MS = 15_000

interface Step { done: boolean; title: string; detail: ReactNode; action?: ReactNode }

const command = (text: string) => <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-foreground">{text}</code>

/** First-run checklist for Overview: hidden until every check has answered, and gone once the archive has sources, articles and an index. */
export function GettingStarted() {
  const poll = (done: boolean | undefined) => (done ? false : POLL_MS)
  const feeds = useQuery({ queryKey: ['getting-started', 'feeds'], queryFn: () => api.feeds(), refetchInterval: query => poll(Boolean(query.state.data?.items.length)) })
  const articles = useQuery({ queryKey: ['getting-started', 'articles'], queryFn: () => api.articles(), refetchInterval: query => poll(Boolean(query.state.data?.items.length)) })
  const index = useQuery({ queryKey: ['indexing-status'], queryFn: api.indexingStatus, retry: false, refetchInterval: query => poll(query.state.data?.index_ready) })
  if (feeds.isPending || articles.isPending || index.isPending) return null

  // A check that failed is left out rather than shown as undone: Overview's own panels report outages.
  const steps: Step[] = []
  if (feeds.isSuccess) steps.push({
    done: feeds.data.items.length > 0, title: 'Add a source',
    detail: 'Add an RSS or Atom feed. NewsIntel polls it on a schedule and keeps every article it finds.',
    action: <Link className={chipClass} href="/sources/">Add a feed</Link>,
  })
  if (articles.isSuccess) steps.push({
    done: articles.data.items.length > 0, title: 'Collect the first articles',
    detail: 'The first poll runs within a minute or two of adding a source. If nothing arrives, the source page shows its fetch errors.',
    action: <Link className={chipClass} href="/articles/">View the collection</Link>,
  })
  if (index.isSuccess) steps.push({
    done: index.data.index_ready, title: 'Build the search index',
    detail: <>Search and the charts below read from Elasticsearch, which starts empty. Run {command('python -m app.cli rebuild-search')} once in the api container.</>,
  })
  if (steps.every(step => step.done)) return null

  const remaining = steps.filter(step => !step.done).length
  return (
    <GlassPanel aria-labelledby="getting-started-title" className="flex flex-col gap-4">
      <div>
        <h3 id="getting-started-title" className="text-sm font-semibold text-foreground">Getting started</h3>
        <p className="mt-1 text-sm text-muted-foreground">{remaining === 1 ? 'One step left' : `${remaining} steps left`} before the archive is ready to explore.</p>
      </div>
      <ol className="flex flex-col gap-3">
        {steps.map(step => (
          <li key={step.title} className="flex gap-3">
            <span aria-hidden="true" className={step.done ? 'mt-0.5 text-primary' : 'mt-0.5 text-muted-foreground'}>{step.done ? '✓' : '○'}</span>
            <div className="flex flex-col items-start gap-1.5 text-sm">
              <p className={step.done ? 'text-muted-foreground line-through' : 'font-medium text-foreground'}>
                {step.title}<span className="sr-only">{step.done ? ' (done)' : ' (to do)'}</span>
              </p>
              {!step.done && <p className="text-muted-foreground">{step.detail}</p>}
              {!step.done && step.action}
            </div>
          </li>
        ))}
      </ol>
    </GlassPanel>
  )
}

/** Says why entity charts, clusters, events and the graph stay empty when the API reports NER off or broken. */
export function EntityExtractionNotice() {
  const nlp = useQuery({ queryKey: ['nlp-status'], queryFn: api.nlpStatus, retry: false })
  const entities = nlp.data?.capabilities.find(capability => capability.name === 'entities')
  if (!entities || entities.state === 'available') return null
  return (
    <GlassPanel role="note" aria-labelledby="entity-notice-title" className="flex flex-col gap-1.5 text-sm">
      <h3 id="entity-notice-title" className="font-semibold text-foreground">{entities.state === 'disabled' ? 'Entity extraction is off' : 'Entity extraction is not working'}</h3>
      <p className="text-muted-foreground">
        Without it, top entities, story clusters, events and the graph stay empty.{' '}
        {entities.state === 'disabled'
          ? <>Start the stack with {command('docker/compose.ner.yaml')} added to your compose files to turn it on.</>
          : <>{entities.detail ?? 'Check the NER configuration.'}</>}
      </p>
    </GlassPanel>
  )
}
