'use client'

import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useEffect } from 'react'
import { legacyHref } from '../../lib/processes'

/** Jobs is part of the Processes page now; old bookmarks land there. */
function Moved() {
  const router = useRouter()
  const target = legacyHref(useSearchParams())
  useEffect(() => { router.replace(target) }, [router, target])
  return <p className="text-sm text-muted-foreground">Jobs moved to <Link className="text-primary hover:underline" href={target}>Processes</Link>.</p>
}

export default function JobsPage() {
  return <Suspense fallback={null}><Moved /></Suspense>
}
