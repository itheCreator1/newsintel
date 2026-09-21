import { expect, it } from 'vitest'
import { changes, monitor } from '../test/monitors'
import { countsLabel, describeChanges, monitorStatus, unseenMonitors } from './monitors'

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

const evidence = [{ article_id: 'a1', title: 'Harbor strike widens' }]

it('words every change from its typed fields alone, in a fixed order', () => {
  const lines = describeChanges(changes({
    article_count: 6,
    sources: [{ source_id: 's1', name: 'Harbor Wire', article_count: 3, evidence }],
    entities: [{ entity_id: 'e1', name: 'Acme', entity_type: 'ORG', article_count: 1, evidence }],
    stories: [
      { cluster_id: 'c1', title: 'Port strike', status: 'new', article_count: 2, source_count: 4, sources_added: 4, evidence },
      { cluster_id: 'c2', title: 'Grid failure', status: 'grew', article_count: 1, source_count: 5, sources_added: 2, evidence },
    ],
  }))

  expect(lines.map(line => line.text)).toEqual([
    '6 new articles',
    'New source: Harbor Wire (3 articles)',
    'New entity: Acme (ORG) — 1 article',
    'New story: Port strike — 4 sources',
    'Story grew: Grid failure — 3 → 5 sources (+2)',
  ])
  expect(lines.map(line => line.subject)).toEqual([{ kind: 'articles' }, { kind: 'source', id: 's1' }, { kind: 'entity', id: 'e1' }, { kind: 'story', id: 'c1' }, { kind: 'story', id: 'c2' }])
  expect(lines[1].evidence).toEqual(evidence)
  expect(describeChanges(changes({ article_count: 6 }))).toEqual(describeChanges(changes({ article_count: 6 })))
})

it('says nothing when nothing changed, and singular when there is one', () => {
  expect(describeChanges(changes())).toEqual([])
  expect(describeChanges(changes({ article_count: 1 }))[0].text).toBe('1 new article')
})

it('names an untitled story and admits when more changes exist than are shown', () => {
  const lines = describeChanges(changes({
    article_count: 9, more_sources: true, more_entities: true, more_stories: true,
    stories: [{ cluster_id: 'c1', title: null, status: 'new', article_count: 1, source_count: 1, sources_added: 1, evidence: [] }],
  }))

  expect(lines[1].text).toBe('New story: Untitled story — 1 source')
  expect(lines.slice(2).map(line => line.text)).toEqual(['More sources matched in this window than are listed.', 'More entities matched in this window than are listed.', 'More stories matched in this window than are listed.'])
})

it('counts enabled monitors with new articles for the navigation badge', () => {
  expect(unseenMonitors([
    monitor({ id: 'a', unseen_article_count: 3 }), monitor({ id: 'b', unseen_article_count: 1 }),
    monitor({ id: 'c', unseen_article_count: 0 }), monitor({ id: 'd', unseen_article_count: 5, enabled: false }),
  ])).toBe(2)
  expect(unseenMonitors(undefined)).toBe(0)
})
