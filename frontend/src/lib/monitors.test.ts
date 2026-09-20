import { expect, it } from 'vitest'
import { monitor } from '../test/monitors'
import { countsLabel, monitorStatus } from './monitors'

const NOW = Date.parse('2026-09-20T12:00:00Z')

it('reads a healthy, evaluated, scheduled monitor as active', () => {
  expect(monitorStatus(monitor(), NOW)).toBe('active')
})

it('is pending once the next evaluation is due, including exactly now', () => {
  expect(monitorStatus(monitor({ next_evaluation_at: '2026-09-20T12:00:00Z' }), NOW)).toBe('pending')
  expect(monitorStatus(monitor({ next_evaluation_at: '2026-09-20T12:00:01Z' }), NOW)).toBe('active')
})

it('waits for the first check before anything else about the schedule', () => {
  expect(monitorStatus(monitor({ evaluated_through: null, next_evaluation_at: '2026-09-20T11:00:00Z' }), NOW)).toBe('waiting')
})

it('prefers paused over error, and error over pending', () => {
  const failing = { error_category: 'search_unavailable', error_message: 'down', next_evaluation_at: '2026-09-20T11:00:00Z' }
  expect(monitorStatus(monitor(failing), NOW)).toBe('error')
  expect(monitorStatus(monitor({ ...failing, enabled: false }), NOW)).toBe('paused')
})

it('is invalid when the stored state can no longer be read, whatever else is true', () => {
  expect(monitorStatus(monitor({ state: null, problem: 'retired', enabled: false, error_category: 'invalid_state' }), NOW)).toBe('invalid')
})

it('words counts as articles and stories, with a zero-story case that is valid', () => {
  expect(countsLabel(monitor({ unseen_article_count: 3, unseen_cluster_count: 0 }))).toBe('3 new articles · 0 new stories')
  expect(countsLabel(monitor({ unseen_article_count: 1, unseen_cluster_count: 1 }))).toBe('1 new article · 1 new story')
})
