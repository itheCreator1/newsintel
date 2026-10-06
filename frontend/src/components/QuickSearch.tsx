'use client'

import { useRouter } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'
import { toHref } from '../lib/investigation'
import { fieldClass } from '../lib/ui-classes'

/** Sidebar search box: Enter opens Search with the query, and "/" focuses it from anywhere that isn't a field. */
export function QuickSearch() {
  const router = useRouter()
  const input = useRef<HTMLInputElement>(null)
  const [q, setQ] = useState('')

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey) return
      if (event.target instanceof Element && event.target.closest('input, select, textarea, [contenteditable="true"]')) return
      event.preventDefault()
      input.current?.focus()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  return (
    <form
      aria-label="Quick search"
      onSubmit={event => {
        event.preventDefault()
        const query = q.trim()
        if (!query) return
        router.push(toHref('/search', new URLSearchParams({ q: query })))
        setQ('')
        input.current?.blur()
      }}
    >
      <label className="sr-only" htmlFor="quick-search">Quick search (press / to focus)</label>
      <input
        ref={input} id="quick-search" type="search" value={q} onChange={event => setQ(event.target.value)}
        placeholder="Search the archive  /" aria-keyshortcuts="/" className={fieldClass} autoComplete="off"
      />
    </form>
  )
}
