'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { api, ApiError } from '../lib/api'
import type { InvestigationState } from '../lib/api-types'
import { monitorKeys } from '../lib/monitors'
import { chipClass, primaryButtonClass } from '../lib/ui-classes'
import { cn } from '../lib/utils'
import { glassPanelClassName } from './GlassPanel'
import { WatchForm } from './WatchForm'

/** Search doubles as the criteria editor: this bar saves the search on screen as the monitor's new criteria, keeping its kind. */
export function EditMonitorBar({ id, state, stopHref }: { id: string; state: InvestigationState; stopHref: string }) {
  const client = useQueryClient()
  const router = useRouter()
  const monitor = useQuery({ queryKey: monitorKeys.detail(id), queryFn: () => api.monitor(id), retry: false })
  const save = useMutation({
    mutationFn: () => api.updateMonitor(id, { kind: monitor.data!.kind, state }),
    onSuccess: updated => {
      client.setQueryData(monitorKeys.detail(id), updated)
      client.invalidateQueries({ queryKey: ['monitors', 'list'] })
      router.push(`/monitors/?id=${encodeURIComponent(id)}`)
    },
  })

  if (monitor.isPending) return <p className="text-sm text-muted-foreground">Loading monitor…</p>
  if (monitor.isError) {
    return (
      <>
        <p role="alert" className="error text-sm text-destructive">This monitor could not be loaded, so its criteria cannot be edited.</p>
        <WatchForm state={state} />
      </>
    )
  }
  const item = monitor.data
  return (
    <div className={cn(glassPanelClassName, 'flex flex-wrap items-center gap-4')}>
      <div className="flex min-w-[220px] flex-1 flex-col gap-1">
        <p className="text-sm text-foreground">Editing the criteria of “{item.name}” ({item.kind})</p>
        <p className="text-xs text-muted-foreground">Saving restarts its counts and history.</p>
      </div>
      <button type="button" className={primaryButtonClass} disabled={save.isPending} onClick={() => save.mutate()}>{save.isPending ? 'Saving…' : 'Save criteria'}</button>
      <Link className={chipClass} href={stopHref}>Stop editing</Link>
      {save.isError && <p role="alert" className="error w-full text-sm text-destructive">{save.error instanceof ApiError ? save.error.message : 'Could not save these criteria.'}</p>}
    </div>
  )
}
