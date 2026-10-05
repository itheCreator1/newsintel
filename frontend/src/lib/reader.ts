import type { ArticleAnnotations } from './api-types'

export type ReaderRange = { start: number; end: number; kind: 'entity' | 'keyword'; id: string; label: string; detail?: string }
export type ReaderSegment = { text: string; range?: ReaderRange }
export type ReaderParagraph = { start: number; segments: ReaderSegment[] }

// The characters Python's `str.split()` treats as whitespace. Not `\s`: JS adds U+FEFF and omits
// U+001C-U+001F, and either difference would shift every later offset.
const PYTHON_WHITESPACE = /[\t-\r\x1c-\x20\x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/

/** Annotation offsets index the NLP input, which is `" ".join(text.split())` counted in code points
 * (backend `nlp/input.py`), not the raw text the reader shows. Entry `i` is the raw UTF-16 offset of
 * cleaned code point `i`; the final entry is the raw end of the last word. */
export function cleanToRawMap(raw: string): number[] {
  const map: number[] = []
  let offset = 0
  let gap: number | null = null
  let end = 0
  for (const char of raw) {
    if (PYTHON_WHITESPACE.test(char)) {
      if (gap === null) gap = offset
    } else {
      if (gap !== null && map.length) map.push(gap)
      gap = null
      map.push(offset)
      end = offset + char.length
    }
    offset += char.length
  }
  map.push(end)
  return map
}

type Highlightable = Pick<ArticleAnnotations, 'entities' | 'keywords' | 'processors'>

/** Entity and keyword occurrences in the body, as non-overlapping ranges in raw-text coordinates.
 * Anything that may describe an older text is left out rather than risk marking the wrong words. */
export function highlightRanges(raw: string, annotations: Highlightable): ReaderRange[] {
  const map = cleanToRawMap(raw)
  const current = (name: string) => annotations.processors.every(item => item.processor !== name || item.completed_generation >= item.requested_generation)
  const candidates: ReaderRange[] = []
  const collect = (kind: ReaderRange['kind'], items: (Highlightable['entities'][number] | Highlightable['keywords'][number])[]) => {
    for (const item of items) {
      if (!item.fresh) continue
      for (const occurrence of item.occurrences) {
        if (occurrence.section !== 'body' || occurrence.start < 0 || occurrence.end <= occurrence.start || occurrence.end >= map.length) continue
        candidates.push({ start: map[occurrence.start], end: map[occurrence.end], kind, id: item.id, label: item.text, ...('entity_type' in item ? { detail: item.entity_type } : {}) })
      }
    }
  }
  if (current('entities')) collect('entity', annotations.entities)
  if (current('keywords')) collect('keyword', annotations.keywords)

  // Entities win an overlap, then the longer span.
  candidates.sort((a, b) => Number(b.kind === 'entity') - Number(a.kind === 'entity') || (b.end - b.start) - (a.end - a.start) || a.start - b.start)
  const kept: ReaderRange[] = []
  for (const candidate of candidates) {
    if (kept.every(range => candidate.end <= range.start || candidate.start >= range.end)) kept.push(candidate)
  }
  return kept.sort((a, b) => a.start - b.start)
}

/** One paragraph per non-empty line (extraction joins lines with `\n`), with `ranges` — sorted and
 * non-overlapping, as `highlightRanges` returns them — clipped to the line they fall in. */
export function paragraphs(raw: string, ranges: ReaderRange[]): ReaderParagraph[] {
  const result: ReaderParagraph[] = []
  for (const match of raw.matchAll(/[^\n]+/g)) {
    if (!match[0].trim()) continue
    const start = match.index
    const end = start + match[0].length
    const segments: ReaderSegment[] = []
    let cursor = start
    for (const range of ranges) {
      const from = Math.max(range.start, start)
      const to = Math.min(range.end, end)
      if (from >= to) continue
      if (from > cursor) segments.push({ text: raw.slice(cursor, from) })
      segments.push({ text: raw.slice(from, to), range })
      cursor = to
    }
    if (cursor < end) segments.push({ text: raw.slice(cursor, end) })
    result.push({ start, segments })
  }
  return result
}

export function readingMinutes(text: string): number {
  return Math.max(1, Math.ceil(text.split(/\s+/).filter(Boolean).length / 200))
}
