import type { Metadata } from 'next'
import type { ReactNode } from 'react'
import { Shell } from '../components/Shell'
import { AuthProvider } from '../lib/auth-context'
import { QueryProvider } from '../lib/query-client'
import '../styles/globals.css'

export const metadata: Metadata = { title: 'NewsIntel' }

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <QueryProvider>
          <AuthProvider>
            <Shell>{children}</Shell>
          </AuthProvider>
        </QueryProvider>
      </body>
    </html>
  )
}
