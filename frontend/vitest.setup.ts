import '@testing-library/jest-dom/vitest'
import { vi } from 'vitest'
import { navigationHarness } from './src/test/navigation-harness'

vi.mock('next/navigation', () => ({
  useRouter: () => navigationHarness,
  usePathname: () => navigationHarness.pathname,
  useSearchParams: () => navigationHarness.searchParams,
}))
