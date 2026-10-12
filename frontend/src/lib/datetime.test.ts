import { act, renderHook } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { afterEach, beforeEach, expect, it } from 'vitest'
import { TIME_ZONE_KEY, browserTimeZone, formatDate, formatDateTime, setTimeZoneChoice, useTimeZone, when } from './datetime'

const AT = '2026-10-12T09:05:00Z'

beforeEach(() => { localStorage.clear(); setTimeZoneChoice('browser') })
afterEach(() => setTimeZoneChoice('browser'))

it('shows dates in the browser\'s zone until another is chosen', () => {
  expect(formatDateTime(AT)).toBe(new Date(AT).toLocaleString(undefined, { timeZone: browserTimeZone() }))

  setTimeZoneChoice('Asia/Tokyo')
  expect(formatDateTime(AT)).toContain('6:05')
  expect(formatDateTime(AT)).toContain('PM')
  setTimeZoneChoice('UTC')
  expect(formatDateTime(AT)).toContain('9:05')
  // Half past eleven at night in UTC is already the next day in Tokyo.
  setTimeZoneChoice('Asia/Tokyo')
  expect(formatDate('2026-10-12T23:30:00Z', { dateStyle: 'long' })).toBe('October 13, 2026')
})

it('says a missing date plainly', () => {
  expect(when(null)).toBe('—')
  expect(when(undefined)).toBe('—')
  setTimeZoneChoice('UTC')
  expect(when(AT)).toBe(formatDateTime(AT))
})

it('remembers the chosen zone in this browser, and ignores one it does not know', () => {
  const { result } = renderHook(() => useTimeZone())
  act(() => result.current.setChoice('Europe/Athens'))
  expect(localStorage.getItem(TIME_ZONE_KEY)).toBe('Europe/Athens')
  expect(result.current).toMatchObject({ choice: 'Europe/Athens', zone: 'Europe/Athens' })

  act(() => result.current.setChoice('browser'))
  expect(localStorage.getItem(TIME_ZONE_KEY)).toBeNull()
  expect(result.current.zone).toBe(browserTimeZone())

  act(() => setTimeZoneChoice('Mars/Olympus_Mons'))
  expect(result.current.zone).toBe(browserTimeZone())
})

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap(name => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return sources(path)
    return /\.tsx?$/.test(path) && !/\.test\.tsx?$/.test(path) ? [path] : []
  })
}

it('formats every date through the chosen zone', () => {
  const root = join(__dirname, '..')
  const direct = sources(root)
    .filter(path => !path.endsWith(join('lib', 'datetime.ts')))
    .flatMap(path => readFileSync(path, 'utf8').split('\n')
      .filter(line => /\.toLocaleString\(\)|\.toLocaleString\(undefined|\.toLocaleDateString\(|\.toLocaleTimeString\(/.test(line))
      .map(line => `${relative(root, path)}: ${line.trim()}`))
  expect(direct).toEqual([])
})
