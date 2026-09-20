'use client'

import { useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { api } from '../lib/api'
import type { Monitor } from '../lib/api-types'
import { clusterHref, entityHref, fromSaved, queryFromState, refine, toHref } from '../lib/investigation'
import { describeChanges, monitorKeys, monitorStatus, type ChangeLine, when } from '../lib/monitors'
import { chipClass } from '../lib/ui-classes'
import { GlassPanel } from './GlassPanel'

const emptyText = (item: Monitor) => {
  const status = monitorStatus(item)
  // A due recount reads empty until it runs, so it must not be reported as "no changes".
  return status === 'pending' ? 'The changes are being refreshed.' : status === 'waiting' ? 'Changes appear after the first check.' : 'No changes since you last looked.'
}

/** What the unseen window brought that this monitor had never matched before, each line linking to its evidence. */
export function MonitorChanges({ id, item, currentHref }: { id: string; item: Monitor; currentHref: string }) {
  const changes = useQuery({ queryKey: monitorKeys.changes(id), queryFn: () => api.monitorChanges(id), retry: false })
  const lines = changes.data ? describeChanges(changes.data) : []
  const subjectHref = (line: ChangeLine) => {
    const subject = line.subject
    if (!subject || subject.kind === 'articles') return null
    if (subject.kind === 'entity') return entityHref(subject.id)
    if (subject.kind === 'story') return clusterHref(subject.id, currentHref)
    return item.state ? toHref('/search', queryFromState(refine(fromSaved(item.state), 'source_id', subject.id))) : null
  }
  return (
    <GlassPanel aria-label="What changed" className="flex flex-col gap-3 py-4">
      <h3 className="text-sm font-semibold text-foreground">What changed</h3>
      {changes.isPending && <p className="text-sm text-muted-foreground">Loading changes…</p>}
      {changes.isError && <p className="error text-sm text-destructive">Could not load the changes.</p>}
      {changes.data?.window_end && <p className="text-xs text-muted-foreground">Counted through {when(changes.data.window_end)}</p>}
      {changes.data && !lines.length && <p className="text-sm text-muted-foreground">{emptyText(item)}</p>}
      {lines.length > 0 && (
        <ul className="flex flex-col gap-3">
          {lines.map(line => {
            const href = subjectHref(line)
            return (
              <li key={line.key} className="flex flex-col gap-1 text-sm text-foreground">
                {href ? <Link className="font-medium hover:underline" href={href}>{line.text}</Link> : <span>{line.text}</span>}
                {line.evidence.length > 0 && (
                  <span className="flex flex-wrap gap-2">
                    {line.evidence.map(article => <Link key={article.article_id} className={chipClass} href={toHref('/articles', new URLSearchParams({ article: article.article_id, from: currentHref }))}>{article.title}</Link>)}
                  </span>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </GlassPanel>
  )
}
