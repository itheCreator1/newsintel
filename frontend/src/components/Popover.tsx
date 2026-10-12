'use client'

import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { cn } from '../lib/utils'
import { ghostButtonClass } from '../lib/ui-classes'

interface PopoverProps {
  /** The opener's text. */
  label: string
  /** The open panel's accessible name. */
  title: string
  className?: string
  children: ReactNode
}

/** A button that opens a small panel beside it; Escape or a click elsewhere closes it. */
export function Popover({ label, title, className, children }: PopoverProps) {
  const [open, setOpen] = useState(false)
  const id = useId()
  const root = useRef<HTMLDivElement>(null)
  const opener = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setOpen(false)
      opener.current?.focus()
    }
    const onPointer = (event: MouseEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false) }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onPointer)
    // The panel's first field takes focus, so the name can be typed at once.
    panel.current?.querySelector<HTMLElement>('input, button')?.focus()
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onPointer)
    }
  }, [open])

  return (
    <div ref={root} className={cn('relative', className)}>
      <button ref={opener} type="button" className={cn(ghostButtonClass, 'h-full px-4 py-2.5 text-sm')} aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen(value => !value)}>{label}</button>
      {open && (
        <div ref={panel} id={id} role="dialog" aria-label={title} className="absolute right-0 z-30 mt-2 w-[min(22rem,calc(100vw-2rem))] rounded-xl border border-border bg-card p-4 shadow-card">
          {children}
        </div>
      )}
    </div>
  )
}
