import { cleanup, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { navigationHarness, resetNavigationHarness } from '../../test/navigation-harness'
import { renderWithQuery } from '../../test/render'
import JobsPage from './page'

afterEach(cleanup)

it('moved to the Processes page: sends the visitor there, keeping the window', async () => {
  resetNavigationHarness({ pathname: '/jobs/', search: 'hours=168' })
  renderWithQuery(() => <JobsPage />)
  expect(await screen.findByRole('link', { name: 'Processes' })).toHaveAttribute('href', '/processes/?hours=168')
  expect(navigationHarness.replace).toHaveBeenCalledWith('/processes/?hours=168')
})
