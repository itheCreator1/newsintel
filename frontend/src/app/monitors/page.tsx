'use client'

import { useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useEffect } from 'react'
import { MonitorDetail } from '../../components/MonitorDetail'
import { toHref } from '../../lib/investigation'

// The watchlist is part of Investigations now; without an id this keeps old bookmarks working.
function ToInvestigations({ order }: { order: string | null }) {
  const router = useRouter()
  useEffect(() => { router.replace(toHref('/investigations', new URLSearchParams(order ? { order } : {}))) }, [router, order])
  return null
}

// Static export has no dynamic routes: `/monitors/?id=<uuid>` is the detail, like `/entities/?id=`.
function MonitorsContent() {
  const params = useSearchParams()
  const id = params.get('id')
  return id ? <MonitorDetail id={id} /> : <ToInvestigations order={params.get('order')} />
}

export default function MonitorsPage() {
  return <Suspense fallback={null}><MonitorsContent /></Suspense>
}
