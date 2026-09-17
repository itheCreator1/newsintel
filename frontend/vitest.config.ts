import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  // next.config.ts sets trailingSlash: true. next/link's href resolution actively ADDS OR STRIPS a
  // trailing slash based on this build-time constant (normally injected by next build/dev), overriding
  // whatever the href prop already contains — so without this, Vitest's bare next/link would silently
  // strip the trailing slash toHref() in lib/investigation.ts deliberately adds.
  define: { 'process.env.__NEXT_TRAILING_SLASH': 'true' },
  test: {
    environment: 'jsdom',
    exclude: ['e2e/**', 'node_modules/**', '.next/**'],
    setupFiles: ['./vitest.setup.ts'],
  },
})
