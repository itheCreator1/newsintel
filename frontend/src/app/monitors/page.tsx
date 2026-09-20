'use client'

import { useSearchParams } from 'next/navigation'
import { Suspense } from 'react'
import { MonitorDetail } from '../../components/MonitorDetail'
import { MonitorList } from '../../components/MonitorList'

// Static export has no dynamic routes: `/monitors/?id=<uuid>` is the detail, like `/entities/?id=`.
function MonitorsContent() {
  const id = useSearchParams().get('id')
  return id ? <MonitorDetail id={id} /> : <MonitorList />
}

export default function MonitorsPage() {
  return <Suspense fallback={null}><MonitorsContent /></Suspense>
}
