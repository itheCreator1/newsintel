import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'
import { navigationHarness } from './src/test/navigation-harness'

vi.mock('next/navigation', () => ({
  useRouter: () => navigationHarness,
  usePathname: () => navigationHarness.pathname,
  useSearchParams: () => navigationHarness.searchParams,
}))

// next/font/google is a Next-compiler macro (SWC transform); it has no meaning outside `next build`/`next dev`.
const mockFont = () => ({ className: '', variable: '', style: { fontFamily: 'sans-serif' } })
vi.mock('next/font/google', () => ({ Manrope: mockFont, JetBrains_Mono: mockFont }))

// Vitest has no globals, so Testing Library never unmounts on its own; a component left mounted can still
// schedule React work after jsdom is torn down (`window is not defined`).
afterEach(cleanup)
