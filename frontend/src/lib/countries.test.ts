import { expect, it } from 'vitest'
import { COUNTRY_OPTIONS, LANGUAGE_OPTIONS, countryLabel, languageLabel, matchesOption } from './countries'

it('names a country with its code, and keeps an unknown code as it is', () => {
  expect(countryLabel('GR')).toBe('Greece (GR)')
  expect(countryLabel('us')).toBe('United States (US)')
  expect(countryLabel('ZZ')).toBe('ZZ')
})

it('offers every country the lexicon knows, sorted by name', () => {
  expect(COUNTRY_OPTIONS).toHaveLength(249)
  const labels = COUNTRY_OPTIONS.map(option => option.label)
  expect(labels).toEqual([...labels].sort((a, b) => a.localeCompare(b)))
  expect(COUNTRY_OPTIONS.find(option => option.value === 'GR')?.label).toBe('Greece (GR)')
})

it('finds a country by its English or Greek name, with or without accents, or by its code', () => {
  const find = (term: string) => COUNTRY_OPTIONS.filter(option => matchesOption(option, term)).map(option => option.value)
  expect(find('greece')).toEqual(['GR'])
  expect(find('Ελλάδα')).toEqual(['GR'])
  expect(find('ελλαδα')).toEqual(['GR'])
  expect(find('gr')).toContain('GR')
  expect(find('γαλλια')).toEqual(['FR'])
  expect(find('γαλλ')).toEqual(expect.arrayContaining(['FR', 'GF', 'PF', 'TF']))
  expect(find('')).toHaveLength(249)
})

it('names a language with its code', () => {
  expect(languageLabel('el')).toBe('Greek (el)')
  expect(languageLabel('und')).toBe('und')
  expect(LANGUAGE_OPTIONS.find(option => option.value === 'en')?.label).toBe('English (en)')
  expect(LANGUAGE_OPTIONS.filter(option => matchesOption(option, 'ελλην')).map(option => option.value)).toEqual(['el'])
})
