import type { BadgeTone } from '../components/StatusBadge'
import type { OpsFeedState, OpsProbe } from './api-types'

export const probeTone = (state: OpsProbe['state']): BadgeTone =>
  ({ ok: 'healthy', degraded: 'degraded', down: 'error', unknown: 'pending' } as const)[state]
export const feedTone = (state: OpsFeedState): BadgeTone =>
  ({ ok: 'healthy', overdue: 'degraded', failing: 'error', awaiting: 'pending', disabled: 'pending' } as const)[state]

/** Seconds as the two largest units ("3 h 5 min"); a dash when the server has no value. */
export function span(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  const total = Math.max(0, Math.floor(seconds))
  const days = Math.floor(total / 86400)
  const hours = Math.floor((total % 86400) / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  if (days) return hours ? `${days} d ${hours} h` : `${days} d`
  if (hours) return minutes ? `${hours} h ${minutes} min` : `${hours} h`
  if (minutes) return `${minutes} min`
  return `${total} s`
}

/** How long before `to` a time was, both taken from the server's clock, so a skewed browser clock cannot mislead. */
export function since(from: string | null | undefined, to: string): string {
  return from ? span((Date.parse(to) - Date.parse(from)) / 1000) : '—'
}

export function bytes(value: number): string {
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1 }
  return unit === 0 ? `${value} B` : `${size.toFixed(1)} ${units[unit]}`
}
