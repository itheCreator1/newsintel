import Link from 'next/link'
import { readerFont } from '../lib/fonts'
import { paragraphs, readingMinutes, type ReaderRange } from '../lib/reader'
import { cn } from '../lib/utils'

type Props = {
  title: string
  /** Extracted text, or `null` before the article has been fetched. */
  text: string | null
  /** Shown in place of the text when there is none: the feed's own description. */
  summary?: string | null
  date: string | null
  sources: string[]
  ranges: ReaderRange[]
  hrefFor: (range: ReaderRange) => string
  className?: string
}

/** The article set as a page to read: a light Newsprint sheet (after Typora's theme of that name)
 * holding only title, byline and body; controls and metadata stay outside it, in the app's own
 * chrome. The title is an `h3` because it sits under the page's own `h2`. */
export function ArticleReader({ title, text, summary, date, sources, ranges, hrefFor, className }: Props) {
  const byline = [sources.join(', '), date && new Date(date).toLocaleDateString(undefined, { dateStyle: 'long' }), text && `${readingMinutes(text)} min read`].filter(Boolean)
  return (
    <article className={cn('reader-newsprint', readerFont.variable, className)}>
      <h3 className="reader-title">{title}</h3>
      {byline.length > 0 && <p className="reader-byline">{byline.join(' · ')}</p>}
      <div className="reader-body">
        {text
          ? paragraphs(text, ranges).map(paragraph => (
            <p key={paragraph.start}>
              {paragraph.segments.map((segment, index) => segment.range
                ? <mark key={index} data-kind={segment.range.kind}><Link href={hrefFor(segment.range)} title={segment.range.detail ? `${segment.range.label} (${segment.range.detail})` : segment.range.label}>{segment.text}</Link></mark>
                : segment.text)}
            </p>
          ))
          : (
            <>
              {summary && <p>{summary}</p>}
              <p className="reader-empty">The full text has not been fetched yet.</p>
            </>
          )}
      </div>
    </article>
  )
}
