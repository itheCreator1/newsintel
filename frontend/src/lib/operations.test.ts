import { expect, it } from 'vitest'
import { AREAS, bytes, feedTone, operationsHref, probeTone, since, span } from './operations'

it('turns seconds into the two largest units, and nothing into a dash', () => {
  expect(span(null)).toBe('—')
  expect(span(undefined)).toBe('—')
  expect(span(0)).toBe('0 s')
  expect(span(42.9)).toBe('42 s')
  expect(span(60)).toBe('1 min')
  expect(span(125)).toBe('2 min')
  expect(span(3600)).toBe('1 h')
  expect(span(3 * 3600 + 5 * 60 + 9)).toBe('3 h 5 min')
  expect(span(86400)).toBe('1 d')
  expect(span(400 * 86400 + 3 * 3600)).toBe('400 d 3 h')
})

it('measures an age against the server time of the response, not the browser clock', () => {
  expect(since('2026-09-21T10:00:00Z', '2026-09-21T10:05:00Z')).toBe('5 min')
  expect(since(null, '2026-09-21T10:05:00Z')).toBe('—')
  expect(since('2026-09-21T10:06:00Z', '2026-09-21T10:05:00Z')).toBe('0 s') // never negative
})

it('formats sizes in binary units', () => {
  expect(bytes(0)).toBe('0 B')
  expect(bytes(1023)).toBe('1023 B')
  expect(bytes(1024)).toBe('1.0 KiB')
  expect(bytes(1536 * 1024)).toBe('1.5 MiB')
  expect(bytes(5 * 1024 ** 3)).toBe('5.0 GiB')
})

it('maps every state to a tone, with unknown never shown as healthy', () => {
  expect(['ok', 'degraded', 'down', 'unknown'].map(state => probeTone(state as never))).toEqual(['healthy', 'degraded', 'error', 'pending'])
  expect((['ok', 'overdue', 'failing', 'awaiting', 'disabled'] as const).map(feedTone)).toEqual(['healthy', 'degraded', 'error', 'pending', 'pending'])
})

it('keeps the window, the failure drill-down and the feed filter in the URL, omitting defaults', () => {
  expect(operationsHref({})).toBe('/operations/')
  expect(operationsHref({ hours: 24 })).toBe('/operations/')
  expect(operationsHref({ hours: 72, area: 'feed', feeds: 'failing' })).toBe('/operations/?hours=72&area=feed&feeds=failing')
  expect(operationsHref({ feeds: 'all' })).toBe('/operations/')
})

it('lists the seven failure areas the API accepts', () => {
  expect(AREAS.map(area => area.value).sort()).toEqual(['article', 'cluster', 'event', 'feed', 'monitor', 'nlp', 'search'])
})
