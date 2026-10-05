import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import type { ReaderRange } from '../lib/reader'
import { ArticleReader } from './ArticleReader'

afterEach(cleanup)

const hrefFor = (range: ReaderRange) => `/${range.kind}/${range.id}/`

it('sets the article as a title, a byline and one paragraph per line', () => {
  const { container } = render(<ArticleReader title="Port delays spread" text={'First line.\n\nSecond line.'} date="2026-09-13T12:00:00Z" sources={['Harbor Wire', 'Harbor Daily']} ranges={[]} hrefFor={hrefFor} />)

  expect(screen.getByRole('heading', { level: 3, name: 'Port delays spread' })).toBeTruthy()
  expect(screen.getByText(/Harbor Wire, Harbor Daily/).textContent).toMatch(/1 min read/)
  expect([...container.querySelectorAll('.reader-body p')].map(node => node.textContent)).toEqual(['First line.', 'Second line.'])
  expect(container.querySelector('mark')).toBeNull()
})

it('marks highlighted ranges as links without changing the text', () => {
  const text = 'Acme signs an energy deal.'
  const ranges: ReaderRange[] = [
    { start: 0, end: 4, kind: 'entity', id: 'e1', label: 'Acme', detail: 'ORG' },
    { start: 14, end: 20, kind: 'keyword', id: 'k1', label: 'energy' },
  ]
  const { container } = render(<ArticleReader title="Deal" text={text} date={null} sources={[]} ranges={ranges} hrefFor={hrefFor} />)

  expect(container.querySelector('.reader-body p')?.textContent).toBe(text)
  expect(screen.getByRole('link', { name: 'Acme' })).toHaveAttribute('href', '/entity/e1/')
  expect(screen.getByRole('link', { name: 'Acme' }).getAttribute('title')).toBe('Acme (ORG)')
  expect(screen.getByRole('link', { name: 'energy' })).toHaveAttribute('href', '/keyword/k1/')
  expect([...container.querySelectorAll('mark')].map(node => node.dataset.kind)).toEqual(['entity', 'keyword'])
})

it('says so when there is no text to read yet', () => {
  render(<ArticleReader title="Deal" text={null} date={null} sources={[]} ranges={[]} hrefFor={hrefFor} />)

  expect(screen.getByText('The full text has not been fetched yet.')).toBeTruthy()
})
