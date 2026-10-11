import { expect, it } from 'vitest'
import { authoritiesHref, authorityFilters, ENTITY_TYPES, readAuthoritiesQuery, typeLabel } from './authorities'

const defaults = { tab: 'names', q: '', language: '', status: undefined, wikidata: undefined, type: [], sort: 'articles', hidden: false, entity: undefined }

it('reads the view from the URL, falling back to the defaults for anything the API would refuse', () => {
  expect(readAuthoritiesQuery(new URLSearchParams(''))).toEqual(defaults)
  expect(readAuthoritiesQuery(new URLSearchParams('tab=changes&q=ai&language=el&status=established&wikidata=unlinked&type=TOPIC&type=ORG&sort=name&hidden=1&entity=e1')))
    .toEqual({ tab: 'changes', q: 'ai', language: 'el', status: 'established', wikidata: 'unlinked', type: ['TOPIC', 'ORG'], sort: 'name', hidden: true, entity: 'e1' })
  expect(readAuthoritiesQuery(new URLSearchParams('tab=nope&status=nope&wikidata=nope&type=WIDGET&type=ORG&sort=nope&hidden=no')))
    .toEqual({ ...defaults, type: ['ORG'] })
})

it('writes the view back to the URL, leaving the defaults out', () => {
  expect(authoritiesHref({})).toBe('/authorities/')
  expect(authoritiesHref({ tab: 'names', sort: 'articles', hidden: false, type: [], q: '' })).toBe('/authorities/')
  expect(authoritiesHref({ tab: 'suggestions', q: 'ai', language: 'el', status: 'provisional', wikidata: 'linked', type: ['ORG', 'TOPIC'], sort: 'name', hidden: true, entity: 'e1' }))
    .toBe('/authorities/?tab=suggestions&q=ai&language=el&status=provisional&wikidata=linked&type=ORG&type=TOPIC&sort=name&hidden=1&entity=e1')
})

it('asks the API for what the view shows, trimmed', () => {
  expect(authorityFilters(readAuthoritiesQuery(new URLSearchParams('')))).toEqual({ sort: 'articles' })
  expect(authorityFilters({ ...readAuthoritiesQuery(new URLSearchParams('')), q: ' hug ', language: ' el ', status: 'provisional', wikidata: 'linked', type: ['ORG'], sort: 'name', hidden: true }))
    .toEqual({ q: 'hug', language: 'el', status: 'provisional', wikidata: 'linked', type: ['ORG'], sort: 'name', hidden: true })
})

it('names every type in words, TOPIC included', () => {
  expect(ENTITY_TYPES).toEqual(['PERSON', 'ORG', 'GPE', 'LOCATION', 'EVENT', 'PRODUCT', 'TOPIC', 'OTHER'])
  expect(ENTITY_TYPES.map(typeLabel)).toEqual(['Person', 'Organization', 'Country or city', 'Place', 'Event', 'Product', 'Topic', 'Other'])
  expect(typeLabel('WORK_OF_ART')).toBe('WORK_OF_ART')
})
