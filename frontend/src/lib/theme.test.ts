import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { THEME_KEY, THEME_SCRIPT, setThemeChoice, useTheme } from './theme'

// jsdom has no matchMedia: a stand-in whose answer and change events the test controls.
let systemDark = false
const mediaListeners = new Set<() => void>()
const html = () => document.documentElement

beforeEach(() => {
  systemDark = false
  mediaListeners.clear()
  localStorage.clear()
  html().className = ''
  html().style.colorScheme = ''
  vi.stubGlobal('matchMedia', (query: string) => ({
    get matches() { return query === '(prefers-color-scheme: dark)' && systemDark },
    addEventListener: (_: string, listener: () => void) => mediaListeners.add(listener),
    removeEventListener: (_: string, listener: () => void) => mediaListeners.delete(listener),
  }))
  setThemeChoice('system')
})
afterEach(() => vi.unstubAllGlobals())

const flipSystem = (dark: boolean) => act(() => { systemDark = dark; mediaListeners.forEach(listener => listener()) })

it('follows the system until a theme is chosen, and keeps following it as it changes', () => {
  const { result } = renderHook(() => useTheme())
  expect(result.current).toMatchObject({ choice: 'system', resolved: 'light' })
  expect(html()).not.toHaveClass('dark')

  flipSystem(true)
  expect(result.current.resolved).toBe('dark')
  expect(html()).toHaveClass('dark')
  expect(html().style.colorScheme).toBe('dark')

  flipSystem(false)
  expect(result.current.resolved).toBe('light')
  expect(html()).not.toHaveClass('dark')
})

it('keeps a chosen theme whatever the system does, and remembers it', () => {
  const { result } = renderHook(() => useTheme())

  act(() => result.current.setChoice('dark'))
  expect(result.current).toMatchObject({ choice: 'dark', resolved: 'dark' })
  expect(localStorage.getItem(THEME_KEY)).toBe('dark')
  expect(html()).toHaveClass('dark')

  act(() => result.current.setChoice('light'))
  flipSystem(true)
  expect(result.current.resolved).toBe('light')
  expect(html()).not.toHaveClass('dark')
  expect(html().style.colorScheme).toBe('light')

  act(() => result.current.setChoice('system'))
  expect(localStorage.getItem(THEME_KEY)).toBeNull()
  expect(result.current.resolved).toBe('dark')
})

it('still switches when the browser refuses storage', () => {
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('denied', 'SecurityError') })
  const { result } = renderHook(() => useTheme())

  act(() => result.current.setChoice('dark'))

  expect(result.current.resolved).toBe('dark')
  expect(html()).toHaveClass('dark')
})

it('sets the theme before the page paints, from the saved choice or the system', () => {
  localStorage.setItem(THEME_KEY, 'dark')
  new Function(THEME_SCRIPT)()
  expect(html()).toHaveClass('dark')
  expect(html().style.colorScheme).toBe('dark')

  html().className = ''
  localStorage.setItem(THEME_KEY, 'light')
  systemDark = true
  new Function(THEME_SCRIPT)()
  expect(html()).not.toHaveClass('dark')

  localStorage.removeItem(THEME_KEY)
  new Function(THEME_SCRIPT)()
  expect(html()).toHaveClass('dark')
})
