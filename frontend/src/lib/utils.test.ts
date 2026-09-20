import { expect, it } from 'vitest'

import { countryName } from './utils'

it('names a country in English, in any case, and falls back to the code', () => {
  expect(countryName('GR')).toBe('Greece')
  expect(countryName('fr')).toBe('France')
  expect(countryName('ZZ')).toBe('Unknown Region')
  expect(countryName('u1')).toBe('u1')
})
