import { cleanup } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { navigationHarness, resetNavigationHarness } from '../../test/navigation-harness'
import { renderWithQuery } from '../../test/render'
import SavedSearchesPage from './page'

afterEach(cleanup)

it('sends the old saved searches address to Investigations, ordered by name', async () => {
  resetNavigationHarness({ pathname: '/saved-searches/' })
  renderWithQuery(() => <SavedSearchesPage />)

  await vi.waitFor(() => expect(navigationHarness.replace).toHaveBeenCalledWith('/investigations/?order=name'))
})
