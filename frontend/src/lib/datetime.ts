'use client'

import { useSyncExternalStore } from 'react'

/** 'browser' follows the zone this browser is in; anything else is an IANA zone name. */
export type TimeZoneChoice = string

export const TIME_ZONE_KEY = 'newsintel-timezone'

const listeners = new Set<() => void>()
let choice: TimeZoneChoice | undefined

export const browserTimeZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'

/** Every zone this browser can show, UTC first. */
export function timeZoneNames(): string[] {
  let zones: string[] = []
  try { zones = Intl.supportedValuesOf('timeZone') } catch { zones = [] }
  return [...new Set(['UTC', browserTimeZone(), ...zones])]
}

function known(zone: string): boolean {
  try {
    new Intl.DateTimeFormat(undefined, { timeZone: zone })
    return true
  } catch {
    return false
  }
}

function readChoice(): TimeZoneChoice {
  try { return localStorage.getItem(TIME_ZONE_KEY) ?? 'browser' } catch { return 'browser' }
}

const currentChoice = () => choice ??= readChoice()
/** The zone dates are shown in; a stored zone this browser does not know falls back to its own. */
export function displayTimeZone(): string {
  const current = currentChoice()
  return current !== 'browser' && known(current) ? current : browserTimeZone()
}

export function setTimeZoneChoice(next: TimeZoneChoice) {
  choice = next
  try {
    if (next === 'browser') localStorage.removeItem(TIME_ZONE_KEY)
    else localStorage.setItem(TIME_ZONE_KEY, next)
  } catch {
    // Storage refused: the choice holds for this page only.
  }
  listeners.forEach(listener => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

const snapshot = () => `${currentChoice()}|${displayTimeZone()}`

export function useTimeZone(): { choice: TimeZoneChoice; zone: string; setChoice: (next: TimeZoneChoice) => void } {
  const [current, zone] = useSyncExternalStore(subscribe, snapshot, () => 'browser|UTC').split('|')
  return { choice: current, zone, setChoice: setTimeZoneChoice }
}

/** A date and time in the chosen zone, in the browser's own format. */
export const formatDateTime = (value: string | number | Date) => new Date(value).toLocaleString(undefined, { timeZone: displayTimeZone() })

export const formatDate = (value: string | number | Date, options: Intl.DateTimeFormatOptions = {}) =>
  new Date(value).toLocaleDateString(undefined, { ...options, timeZone: displayTimeZone() })

/** A date and time, or a dash when there is none. */
export const when = (value: string | null | undefined) => value ? formatDateTime(value) : '—'
