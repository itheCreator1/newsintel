import { expect, it } from 'vitest'
import { activityBadge, cardBadge, itemHref, legacyHref, processesHref, readProcessesQuery } from './processes'

const card = (over = {}) => ({ key: 'articles', group: 'per_item', label: 'Article download', description: '', state: 'ok', actions: [], ...over }) as never

it('reads the view from the URL, falling back to the defaults for anything the API would refuse', () => {
  expect(readProcessesQuery(new URLSearchParams(''))).toEqual({ hours: 24, process: undefined, status: 'attention', q: '', feeds: 'all' })
  expect(readProcessesQuery(new URLSearchParams('hours=168&process=nlp&status=failed&q=Kathimerini&feeds=attention')))
    .toEqual({ hours: 168, process: 'nlp', status: 'failed', q: 'Kathimerini', feeds: 'attention' })
  expect(readProcessesQuery(new URLSearchParams('hours=72&process=nonsense&status=nonsense&feeds=nonsense')))
    .toEqual({ hours: 24, process: undefined, status: 'attention', q: '', feeds: 'all' })
})

it('writes the view back to the URL, leaving the defaults out', () => {
  expect(processesHref({})).toBe('/processes/')
  expect(processesHref({ hours: 24, status: 'attention', feeds: 'all', q: '' })).toBe('/processes/')
  expect(processesHref({ hours: 1, process: 'feeds', status: 'all', q: 'wire', feeds: 'attention' }))
    .toBe('/processes/?hours=1&process=feeds&status=all&q=wire&feeds=attention')
})

it('sends the old Jobs and Operations addresses to the Processes page, keeping a window it still offers', () => {
  expect(legacyHref(new URLSearchParams(''))).toBe('/processes/')
  expect(legacyHref(new URLSearchParams('hours=168&area=feed'))).toBe('/processes/?hours=168')
  expect(legacyHref(new URLSearchParams('hours=72'))).toBe('/processes/')
})

it('links each row to what it is about', () => {
  expect(itemHref('article', 'a1')).toBe('/articles/?article=a1')
  expect(itemHref('feed', 'f1')).toBe('/sources/detail/?id=f1')
  expect(itemHref('entity', 'e1')).toBe('/entities/?id=e1')
  expect(itemHref('monitor', 'm1')).toBe('/monitors/?id=m1')
  expect(itemHref(null, null)).toBeNull()
  expect(itemHref('article', null)).toBeNull()
})

it('names each card state with a tone, and a bulk run in hand by its progress', () => {
  expect(cardBadge(card({ state: 'ok' }))).toEqual({ tone: 'healthy', label: 'Up to date' })
  expect(cardBadge(card({ state: 'working' }))).toEqual({ tone: 'active', label: 'Working' })
  expect(cardBadge(card({ state: 'retrying' }))).toEqual({ tone: 'degraded', label: 'Retrying' })
  expect(cardBadge(card({ state: 'stalled' }))).toEqual({ tone: 'degraded', label: 'Stalled' })
  expect(cardBadge(card({ state: 'failing', failed_in_window: 3 }))).toEqual({ tone: 'error', label: '3 failed' })
  expect(cardBadge(card({ state: 'failing', group: 'scheduled' }))).toEqual({ tone: 'error', label: 'Failed' })
  expect(cardBadge(card({ state: 'idle' }))).toEqual({ tone: 'pending', label: 'Idle' })
  expect(cardBadge(card({ state: 'off' }))).toEqual({ tone: 'pending', label: 'Off' })
  expect(cardBadge(card({ state: 'working', group: 'bulk', progress: { done: 29840, total: 48210 } }))).toEqual({ tone: 'active', label: '62%' })
  expect(cardBadge(card({ state: 'working', group: 'bulk', progress: { done: 10, total: null } }))).toEqual({ tone: 'active', label: 'Working' })
  expect(cardBadge(card({ state: 'working', group: 'bulk', progress: { done: 0, total: 0 } }))).toEqual({ tone: 'active', label: 'Working' })
})

it('names each row status with a tone', () => {
  expect(['failed', 'retrying', 'running', 'queued', 'succeeded', 'stopped'].map(status => activityBadge(status as never))).toEqual([
    { tone: 'error', label: 'Failed' }, { tone: 'degraded', label: 'Retrying' }, { tone: 'active', label: 'Running' },
    { tone: 'pending', label: 'Queued' }, { tone: 'healthy', label: 'Done' }, { tone: 'pending', label: 'Stopped' },
  ])
})
