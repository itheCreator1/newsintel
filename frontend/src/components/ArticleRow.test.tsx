import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { ArticleRow } from './ArticleRow'

afterEach(cleanup)

const article = { id: 'a1', title: 'Harbour talks', published_at: '2026-09-18T10:00:00Z', first_discovered_at: '2026-09-18T11:00:00Z' }

it('links the title and dates the article, with the reader\'s star and read state', () => {
  render(<ArticleRow article={{ ...article, read: true, starred: true }} href="/articles/?article=a1" />)

  expect(screen.getByRole('link', { name: 'Harbour talks' })).toHaveAttribute('href', '/articles/?article=a1')
  expect(screen.getByRole('img', { name: 'Starred' })).toBeTruthy()
  expect(screen.getByText(/ · Read$/)).toBeTruthy()
})

it('shows neither mark on an article the reader has not touched, and keeps extra details', () => {
  render(<ArticleRow article={{ ...article, published_at: null }} href="/x">{' · Story'}</ArticleRow>)

  expect(screen.queryByRole('img', { name: 'Starred' })).toBeNull()
  expect(screen.queryByText(/Read/)).toBeNull()
  expect(screen.getByText(/ · Story$/)).toBeTruthy()
})
