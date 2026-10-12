'use client'

import { useRouter } from 'next/navigation'
import { useEffect } from 'react'

// Saved searches now live on Investigations; this keeps old bookmarks working.
export default function SavedSearchesPage() {
  const router = useRouter()
  useEffect(() => { router.replace('/investigations/?order=name') }, [router])
  return null
}
