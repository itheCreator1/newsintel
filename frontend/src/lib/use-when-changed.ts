import { useState } from 'react'

/**
 * Calls `adjust` during the render in which `key` first differs from the one before, never on the first
 * render. It is React's way to reset state when a prop or the URL changes: an effect would render the stale
 * state once and then again. `adjust` may only set this component's own state.
 */
export function useWhenChanged(key: unknown, adjust: () => void) {
  const [seen, setSeen] = useState(key)
  if (!Object.is(seen, key)) {
    setSeen(key)
    adjust()
  }
}
