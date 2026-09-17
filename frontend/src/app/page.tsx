'use client'

import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'

export default function OverviewPage() {
  const status = useQuery({ queryKey: ['status'], queryFn: api.status })
  return (
    <>
      <header><div><p className="eyebrow">System overview</p><h2>Archive operations</h2></div></header>
      <section className="status-card">
        <span className="status-dot" />
        <div><strong>Core services</strong><p>{status.data?.status || 'Checking'}</p></div>
      </section>
    </>
  )
}
