import type { Metadata } from 'next'
import type { ReactNode } from 'react'
import { Shell } from '../components/Shell'
import { AuthProvider } from '../lib/auth-context'
import { QueryProvider } from '../lib/query-client'
import { THEME_SCRIPT, ThemeSync } from '../lib/theme'
import '../styles/globals.css'

export const metadata: Metadata = { title: 'NewsIntel' }

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // The head script sets the theme class before React runs, so <html> differs from the static export on purpose.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>
        <ThemeSync />
        <QueryProvider>
          <AuthProvider>
            <Shell>{children}</Shell>
          </AuthProvider>
        </QueryProvider>
      </body>
    </html>
  )
}
