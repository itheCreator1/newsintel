'use client'

import { useEffect } from 'react'
import { ErrorNotice } from '../components/Feedback'
import { GlassPanel } from '../components/GlassPanel'
import { PageHeader } from '../components/PageHeader'

/** Route error boundary: a render exception replaces the page, not the shell around it. */
export default function RouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => { console.error(error) }, [error])
  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="Error" title="Something went wrong" />
      <GlassPanel><ErrorNotice message="This page failed to render." onRetry={reset} /></GlassPanel>
    </div>
  )
}
