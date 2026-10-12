import Link from 'next/link'
import type { ReactNode } from 'react'
import { when } from '../lib/datetime'
import { cn } from '../lib/utils'

export interface RowArticle {
  title: string
  published_at?: string | null
  first_discovered_at?: string | null
  read?: boolean
  starred?: boolean
}

/** One article in a list: its title, its date and the reader's own marks, the same on every page. */
export function ArticleRow({ article, href, as: Tag = 'div', className, compact = false, children }: {
  article: RowArticle
  href: string
  as?: 'div' | 'li'
  className?: string
  /** Smaller type, for lists inside a side panel. */
  compact?: boolean
  /** More of the date line, after the date and the read state. */
  children?: ReactNode
}) {
  return (
    <Tag className={cn('flex flex-col gap-0.5', className)}>
      <span className="flex items-start gap-2">
        <Link className={cn('font-semibold text-foreground hover:underline', compact ? 'text-sm' : 'text-[15px]')} href={href}>{article.title}</Link>
        {article.starred && <span className="ml-auto text-primary" role="img" aria-label="Starred">★</span>}
      </span>
      <span className="text-xs text-muted-foreground">{when(article.published_at ?? article.first_discovered_at)}{article.read && ' · Read'}{children}</span>
    </Tag>
  )
}
