'use client'

import { Suspense } from 'react'
import { InvestigationList } from '../../components/InvestigationList'

export default function InvestigationsPage() {
  return <Suspense fallback={null}><InvestigationList /></Suspense>
}
