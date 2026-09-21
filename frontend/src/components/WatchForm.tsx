'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { InvestigationState, MonitorKind } from '../lib/api-types'
import { monitorKeys } from '../lib/monitors'
import { chipClass, fieldClass, labelClass, primaryButtonClass } from '../lib/ui-classes'
import { cn } from '../lib/utils'
import { glassPanelClassName } from './GlassPanel'

interface WatchFormProps {
  state: InvestigationState
  kind?: MonitorKind
  defaultName?: string
  label?: string
  className?: string
}

/** Turns what is on screen into a monitor; the state is copied, so later edits to anything else never change what it watches. */
export function WatchForm({ state, kind = 'search', defaultName = '', label = 'Watch search', className }: WatchFormProps) {
  const client = useQueryClient()
  const [name, setName] = useState(defaultName)
  const watch = useMutation({ mutationFn: (value: string) => api.createMonitor(value, state, kind), onSuccess: () => client.invalidateQueries({ queryKey: monitorKeys.all }) })

  return (
    <form className={cn(className ?? glassPanelClassName, 'flex flex-wrap items-end gap-4')} onSubmit={event => { event.preventDefault(); if (name.trim()) watch.mutate(name.trim()) }}>
      <label className={cn(labelClass, 'min-w-[220px] flex-1')}>Monitor name<input className={cn(fieldClass, 'mt-1')} value={name} onChange={e => setName(e.target.value)} maxLength={120} placeholder="Energy grid watch" /></label>
      <button type="submit" disabled={watch.isPending} className={primaryButtonClass}>{watch.isPending ? 'Watching…' : label}</button>
      {watch.isError ? <p role="alert" className="error w-full text-sm text-destructive">{watch.error instanceof ApiError ? watch.error.message : 'Could not create this monitor.'}</p>
        : watch.isSuccess && <p role="status" className="w-full text-sm text-primary">Watching “{watch.variables}”. <Link className={chipClass} href="/monitors/">Open watchlist</Link></p>}
    </form>
  )
}
