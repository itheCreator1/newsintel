'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { InvestigationState } from '../lib/api-types'
import { monitorKeys } from '../lib/monitors'
import { chipClass, fieldClass, labelClass, primaryButtonClass } from '../lib/ui-classes'
import { cn } from '../lib/utils'
import { glassPanelClassName } from './GlassPanel'

/** Turns the search on screen into a monitor; the state is copied, so later edits to anything else never change what it watches. */
export function WatchSearchForm({ state }: { state: InvestigationState }) {
  const client = useQueryClient()
  const [name, setName] = useState('')
  const watch = useMutation({ mutationFn: (value: string) => api.createMonitor(value, state), onSuccess: () => client.invalidateQueries({ queryKey: monitorKeys.all }) })

  return (
    <form className={cn(glassPanelClassName, 'flex flex-wrap items-end gap-4')} onSubmit={event => { event.preventDefault(); if (name.trim()) watch.mutate(name.trim()) }}>
      <label className={cn(labelClass, 'min-w-[220px] flex-1')}>Monitor name<input className={cn(fieldClass, 'mt-1')} value={name} onChange={e => setName(e.target.value)} maxLength={120} placeholder="Energy grid watch" /></label>
      <button type="submit" disabled={watch.isPending} className={primaryButtonClass}>{watch.isPending ? 'Watching…' : 'Watch search'}</button>
      {watch.isError ? <p role="alert" className="error w-full text-sm text-destructive">{watch.error instanceof ApiError ? watch.error.message : 'Could not watch this search.'}</p>
        : watch.isSuccess && <p role="status" className="w-full text-sm text-primary">Watching “{watch.variables}”. <Link className={chipClass} href="/monitors/">Open watchlist</Link></p>}
    </form>
  )
}
