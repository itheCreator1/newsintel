'use client'

import type { ExportFormat } from '../lib/api'
import { chipClass } from '../lib/ui-classes'
import { Popover } from './Popover'

/** The same Export button wherever a list of articles can be downloaded. */
export function ExportMenu({ hrefFor, className }: { hrefFor(format: ExportFormat): string; className?: string }) {
  return (
    <Popover label="Export" title="Export articles" className={className}>
      <div className="flex flex-col gap-3">
        <div className="flex gap-2">
          <a className={chipClass} href={hrefFor('csv')} download>CSV</a>
          <a className={chipClass} href={hrefFor('json')} download>JSON</a>
        </div>
        <p className="text-xs text-muted-foreground">Up to 5,000 articles, in this order.</p>
      </div>
    </Popover>
  )
}
