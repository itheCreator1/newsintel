'use client'

import { useSyncExternalStore } from 'react'

export type ThemeChoice = 'system' | 'light' | 'dark'
export type Theme = 'light' | 'dark'

export const THEME_KEY = 'newsintel-theme'
const DARK_QUERY = '(prefers-color-scheme: dark)'

/** Runs in <head> before the first paint, so a dark page never flashes light. Mirrors `resolved` and `apply` below. */
export const THEME_SCRIPT = `try{var c=localStorage.getItem(${JSON.stringify(THEME_KEY)});var d=c==='dark'||(c!=='light'&&window.matchMedia(${JSON.stringify(DARK_QUERY)}).matches);document.documentElement.classList.toggle('dark',d);document.documentElement.style.colorScheme=d?'dark':'light'}catch(e){}`

const listeners = new Set<() => void>()
let choice: ThemeChoice | undefined

function readChoice(): ThemeChoice {
  try {
    const saved = localStorage.getItem(THEME_KEY)
    return saved === 'light' || saved === 'dark' ? saved : 'system'
  } catch {
    return 'system'
  }
}

const media = () => typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(DARK_QUERY) : undefined
const currentChoice = () => choice ??= readChoice()
const resolved = (): Theme => {
  const current = currentChoice()
  return current === 'system' ? (media()?.matches ? 'dark' : 'light') : current
}

function apply() {
  const theme = resolved()
  document.documentElement.classList.toggle('dark', theme === 'dark')
  document.documentElement.style.colorScheme = theme
}

function changed() {
  apply()
  listeners.forEach(listener => listener())
}

function fromOtherTab(event: StorageEvent) {
  if (event.key !== THEME_KEY) return
  choice = readChoice()
  changed()
}

/** The browser keeps the choice; the system answer is read live, so "system" follows it as it changes. */
export function setThemeChoice(next: ThemeChoice) {
  choice = next
  try {
    if (next === 'system') localStorage.removeItem(THEME_KEY)
    else localStorage.setItem(THEME_KEY, next)
  } catch {
    // Storage refused (private mode, a policy): the choice holds for this page only.
  }
  changed()
}

function subscribe(listener: () => void) {
  if (!listeners.size) {
    media()?.addEventListener('change', changed)
    window.addEventListener('storage', fromOtherTab)
  }
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
    if (!listeners.size) {
      media()?.removeEventListener('change', changed)
      window.removeEventListener('storage', fromOtherTab)
    }
  }
}

const snapshot = () => `${currentChoice()}:${resolved()}`

export function useTheme(): { choice: ThemeChoice; resolved: Theme; setChoice: (next: ThemeChoice) => void } {
  const [current, theme] = useSyncExternalStore(subscribe, snapshot, () => 'system:light').split(':') as [ThemeChoice, Theme]
  return { choice: current, resolved: theme, setChoice: setThemeChoice }
}

/** Mounted once in the layout, so the page follows the system even where nothing else reads the theme. */
export function ThemeSync() {
  useTheme()
  return null
}
