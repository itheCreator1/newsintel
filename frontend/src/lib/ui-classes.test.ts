import { expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { chipClass } from './ui-classes'

// styles/globals.css skips Tailwind's Preflight and styles every <button> as a filled primary
// button, so a class string has to undo what it does not want from that base.

it('keeps a chip button unfilled and regular weight over the global button style', () => {
  const classes = chipClass.split(' ')
  expect(classes).toContain('bg-transparent')
  expect(classes).toContain('font-normal')
})

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap(name => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return sources(path)
    return path.endsWith('.tsx') && !path.endsWith('.test.tsx') ? [path] : []
  })
}

it('drops the browser list markers from every styled list, which Preflight would have removed', () => {
  const root = join(__dirname, '..')
  const unmarked = sources(root).flatMap(path =>
    [...readFileSync(path, 'utf8').matchAll(/<ul\b[^>]*className="([^"]*)"/g)]
      .filter(match => !match[1].split(' ').includes('list-none'))
      .map(match => `${relative(root, path)}: ${match[1]}`))
  expect(unmarked).toEqual([])
})
