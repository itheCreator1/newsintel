import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, it } from 'vitest'

const css = readFileSync(join(__dirname, 'globals.css'), 'utf8')
// The palette block of a selector: the one that sets --background (the legacy sheet has its own :root rule).
const tokens = (selector: string) => {
  const blocks = css.split(`${selector} {`).slice(1).map(rest => rest.slice(0, rest.indexOf('}')))
  const block = blocks.find(body => body.includes('--background:'))
  expect(block, `${selector} palette`).toBeDefined()
  return [...block!.matchAll(/(--[\w-]+):/g)].map(match => match[1]).sort()
}

it('gives the dark theme a value for every colour token of the light one', () => {
  const light = tokens(':root').filter(name => name !== '--radius')
  expect(light).toContain('--primary')
  expect(tokens('.dark')).toEqual(expect.arrayContaining(light))
  expect(css).toMatch(/\.dark\s*\{[^}]*color-scheme:\s*dark/)
})

it('gives the article reader its own dark page', () => {
  expect(css).toMatch(/\.dark \.reader-newsprint\s*\{[^}]*background:/)
})
