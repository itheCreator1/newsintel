import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { SearchFacets } from '../lib/api-types'
import { stateFromQuery } from '../lib/investigation'
import { FacetPanel } from './FacetPanel'

const empty = { buckets: [], truncated: false }
const facets: SearchFacets = {
  total: 6,
  sources: { buckets: [{ value: 's1', label: 'Harbor Wire', count: 3 }, { value: 's2', label: 'Harbor Daily', count: 3 }], truncated: true },
  source_countries: empty, story_countries: { buckets: [{ value: 'GR', label: null, count: 2 }], truncated: false },
  mentioned_countries: empty, languages: empty, entities: empty, entity_types: empty, keywords: empty, story_clusters: empty,
}
afterEach(cleanup)

it('lists non-empty groups with counts, marks applied values, and notes truncation', () => {
  render(<FacetPanel facets={facets} state={stateFromQuery(new URLSearchParams('source_id=s1'))} onToggle={vi.fn()} />)
  const sources = screen.getByRole('region', { name: 'Sources' })
  expect(within(sources).getByRole('button', { name: 'Harbor Wire 3' }).getAttribute('aria-pressed')).toBe('true')
  expect(within(sources).getByRole('button', { name: 'Harbor Daily 3' }).getAttribute('aria-pressed')).toBe('false')
  expect(within(sources).getByText('Top 2 shown.')).toBeTruthy()
  expect(within(screen.getByRole('region', { name: 'Story countries' })).getByRole('button', { name: 'GR 2' })).toBeTruthy()
  expect(screen.queryByRole('region', { name: 'Keywords' })).toBeNull()
})

it('toggles the value on its URL field', () => {
  const onToggle = vi.fn()
  render(<FacetPanel facets={facets} state={stateFromQuery(new URLSearchParams())} onToggle={onToggle} />)
  fireEvent.click(screen.getByRole('button', { name: 'GR 2' }))
  expect(onToggle).toHaveBeenCalledWith('story_country', 'GR')
  fireEvent.click(screen.getByRole('button', { name: 'Harbor Daily 3' }))
  expect(onToggle).toHaveBeenLastCalledWith('source_id', 's2')
})

it('says so when the search has no facet values', () => {
  const none = { ...facets, total: 0, sources: empty, story_countries: empty }
  render(<FacetPanel facets={none} state={stateFromQuery(new URLSearchParams())} onToggle={vi.fn()} />)
  expect(screen.getByText('No facets for this search.')).toBeTruthy()
})
