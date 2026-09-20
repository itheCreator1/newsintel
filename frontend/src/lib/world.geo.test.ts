import { expect, it } from 'vitest'

import world from './world.geo.json'

const codes = world.features.map(feature => feature.properties.code)

it('draws each country once under its two-letter code, including those Natural Earth leaves as -99', () => {
  expect(codes.every(code => /^[A-Z]{2}$/.test(code))).toBe(true)
  expect(new Set(codes).size).toBe(codes.length)
  for (const code of ['FR', 'NO', 'TW', 'GR', 'US', 'GB']) expect(codes).toContain(code)
})

it('is self-hosted geometry only, with nothing to fetch', () => {
  expect(JSON.stringify(world)).not.toMatch(/https?:\/\//)
  expect(world.features.every(feature => ['Polygon', 'MultiPolygon'].includes(feature.geometry.type))).toBe(true)
})
