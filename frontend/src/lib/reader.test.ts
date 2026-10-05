import { expect, it } from 'vitest'

import { cleanToRawMap, highlightRanges, paragraphs, readingMinutes } from './reader'

const occurrence = (start: number, end: number, section = 'body') => ({ section, reference_id: null, start, end, input_start: start, input_end: end })
const entity = (id: string, text: string, occurrences: ReturnType<typeof occurrence>[], fresh = true) => ({ id, text, normalized_text: text.toLowerCase(), entity_type: 'ORG', original_label: 'ORG', relevance: 1, occurrence_count: occurrences.length, occurrences, fresh })
const keyword = (id: string, text: string, occurrences: ReturnType<typeof occurrence>[], fresh = true) => ({ id, text, normalized_text: text, kind: 'keyphrase', relevance: 1, raw_score: 0.1, occurrence_count: occurrences.length, occurrences, fresh })
const processor = (name: string, completed: number, requested: number) => ({ processor: name, status: 'completed', requested_generation: requested, completed_generation: completed, processor_version: '1', algorithm_version: null, model_version: null, configuration_fingerprint: 'c', input_fingerprint: 'i', completed_at: null, detail: null })
const slices = (raw: string, ranges: { start: number; end: number }[]) => ranges.map(range => raw.slice(range.start, range.end))

it('maps whitespace-collapsed offsets back onto the raw text', () => {
  const raw = '  Acme  buys\n\nGlobex \n'
  // Python: " ".join(raw.split()) == "Acme buys Globex"
  const map = cleanToRawMap(raw)

  expect(map).toHaveLength('Acme buys Globex'.length + 1)
  expect(raw.slice(map[0], map[4])).toBe('Acme')
  expect(raw.slice(map[5], map[9])).toBe('buys')
  expect(raw.slice(map[10], map[16])).toBe('Globex')
})

it('counts an astral character as one offset, as Python does', () => {
  const raw = '🚀 Acme lands'
  // Python offsets: 🚀=0, space=1, Acme=2..6
  const ranges = highlightRanges(raw, { entities: [entity('e1', 'Acme', [occurrence(2, 6)])], keywords: [], processors: [] })

  expect(slices(raw, ranges)).toEqual(['Acme'])
})

it('keeps only in-range body occurrences', () => {
  const raw = 'Acme buys Globex'
  const ranges = highlightRanges(raw, {
    entities: [entity('e1', 'Acme', [occurrence(0, 4), occurrence(0, 4, 'title'), occurrence(10, 99), occurrence(6, 6)])],
    keywords: [], processors: [],
  })

  expect(ranges).toEqual([{ start: 0, end: 4, kind: 'entity', id: 'e1', label: 'Acme', detail: 'ORG' }])
})

it('prefers the entity where an entity and a keyword overlap, and orders by position', () => {
  const raw = 'Acme energy deal with Globex'
  const ranges = highlightRanges(raw, {
    entities: [entity('e2', 'Globex', [occurrence(22, 28)]), entity('e1', 'Acme', [occurrence(0, 4)])],
    keywords: [keyword('k1', 'Acme energy', [occurrence(0, 11)]), keyword('k2', 'deal', [occurrence(12, 16)])],
    processors: [],
  })

  expect(ranges.map(range => [range.kind, raw.slice(range.start, range.end)])).toEqual([['entity', 'Acme'], ['keyword', 'deal'], ['entity', 'Globex']])
})

it('skips stale annotations and kinds whose processor has not caught up', () => {
  const raw = 'Acme energy deal'
  const annotations = {
    entities: [entity('e1', 'Acme', [occurrence(0, 4)], false)],
    keywords: [keyword('k1', 'energy', [occurrence(5, 11)])],
  }

  expect(slices(raw, highlightRanges(raw, { ...annotations, processors: [] }))).toEqual(['energy'])
  expect(highlightRanges(raw, { ...annotations, processors: [processor('keywords', 1, 2)] })).toEqual([])
})

it('splits lines into paragraphs and clips ranges to each one', () => {
  const raw = 'Acme buys\n\nGlobex today'
  const ranges = highlightRanges(raw, {
    // "Acme buys Globex today": one occurrence runs across the paragraph break.
    entities: [entity('e1', 'buys Globex', [occurrence(5, 16)])], keywords: [], processors: [],
  })

  expect(paragraphs(raw, ranges).map(paragraph => paragraph.segments.map(segment => [segment.text, segment.range?.id ?? null]))).toEqual([
    [['Acme ', null], ['buys', 'e1']],
    [['Globex', 'e1'], [' today', null]],
  ])
})

it('renders unannotated text as one segment per non-empty line', () => {
  expect(paragraphs('One\n\n \nTwo', []).map(paragraph => paragraph.segments)).toEqual([[{ text: 'One' }], [{ text: 'Two' }]])
})

it('estimates reading time at 200 words a minute, never below one', () => {
  expect(readingMinutes('short')).toBe(1)
  expect(readingMinutes(Array(401).fill('word').join(' '))).toBe(3)
})
