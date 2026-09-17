'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import type { ComponentProps } from 'react'
import { cn } from '../lib/utils'

const normalize = (path: string) => path.length > 1 && path.endsWith('/') ? path.slice(0, -1) : path

/** Wraps next/link with vue-router's default RouterLink active-class behavior: exact match only, so `/` never lights up for every route. */
export function NavLink({ href, className, children, ...rest }: ComponentProps<typeof Link>) {
  const pathname = usePathname()
  const isActive = normalize(pathname) === normalize(String(href))
  return (
    <Link
      href={href}
      className={cn(
        isActive ? 'router-link-active' : undefined,
        'rounded-lg px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground',
        isActive && 'bg-primary/12 text-foreground shadow-[inset_0_0_0_1px_rgb(140_165_255/18%)]',
        className,
      )}
      {...rest}
    >
      {children}
    </Link>
  )
}
