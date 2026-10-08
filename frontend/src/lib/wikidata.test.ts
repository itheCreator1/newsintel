import { expect, it } from 'vitest'
import { identifierLinks, itemText, qidHref, reasonText, wikidataChange } from './wikidata'

it('states each candidate reason in words', () => {
  expect(reasonText('label:en')).toBe('Exact label (en)')
  expect(reasonText('alias:el')).toBe('Exact alias (el)')
  expect(reasonText('variant:el')).toBe('el label is already a name')
  expect(reasonText('type_matches')).toBe('Type matches')
  expect(reasonText('type_differs')).toBe('Type differs')
  expect(reasonText('sitelinks:1')).toBe('1 sitelink')
  expect(reasonText('sitelinks:312')).toBe('312 sitelinks')
  expect(reasonText('something_new')).toBe('something_new')
})

it('links the item and the identifiers read off it, in a fixed order', () => {
  expect(qidHref('Q42')).toBe('https://www.wikidata.org/wiki/Q42')
  expect(identifierLinks({ lcnaf: 'n80076765', isni: '0000 0001 2144 1970', viaf: '113230702' })).toEqual([
    { scheme: 'viaf', label: 'VIAF', value: '113230702', href: 'https://viaf.org/viaf/113230702' },
    { scheme: 'isni', label: 'ISNI', value: '0000 0001 2144 1970', href: 'https://isni.org/isni/0000000121441970' },
    { scheme: 'lcnaf', label: 'LCNAF', value: 'n80076765', href: 'https://id.loc.gov/authorities/names/n80076765.html' },
  ])
  expect(identifierLinks({})).toEqual([])
})

it('reads a label or description in the entity language, then English, then Greek', () => {
  expect(itemText({ el: 'Αθήνα', en: 'Athens' }, 'el')).toBe('Αθήνα')
  expect(itemText({ el: 'Αθήνα', en: 'Athens' }, 'fr')).toBe('Athens')
  expect(itemText({ el: 'Αθήνα' }, 'en')).toBe('Αθήνα')
  expect(itemText({}, 'en')).toBeNull()
})

it('describes the Wikidata history entries and leaves the rest alone', () => {
  expect(wikidataChange({ action: 'wikidata_linked', before: null, after: { qid: 'Q42' } })).toBe('Linked to Wikidata Q42')
  expect(wikidataChange({ action: 'wikidata_unlinked', before: { qid: 'Q42' }, after: null })).toBe('Unlinked from Wikidata Q42')
  expect(wikidataChange({ action: 'wikidata_redirected', before: { qid: 'Q1' }, after: { qid: 'Q2' } }))
    .toBe('Wikidata merged Q1 into Q2; the link moved')
  expect(wikidataChange({ action: 'wikidata_missing', before: { qid: 'Q9' }, after: null }))
    .toBe('Wikidata deleted Q9; the link is kept')
  expect(wikidataChange({ action: 'merged', before: null, after: null })).toBeNull()
})
