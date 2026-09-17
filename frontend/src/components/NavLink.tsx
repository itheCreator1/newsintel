'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import type { ComponentProps } from 'react'

const normalize = (path: string) => path.length > 1 && path.endsWith('/') ? path.slice(0, -1) : path

/** Wraps next/link with vue-router's default RouterLink active-class behavior: exact match only, so `/` never lights up for every route. */
export function NavLink({ href, children, ...rest }: ComponentProps<typeof Link>) {
  const pathname = usePathname()
  const isActive = normalize(pathname) === normalize(String(href))
  return <Link href={href} className={isActive ? 'router-link-active' : undefined} {...rest}>{children}</Link>
}
