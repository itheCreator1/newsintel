import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  output: 'export',
  // Default static export emits /route.html, not /route/index.html, which does not match
  // infra/nginx.conf's `try_files $uri $uri/ /index.html;`. trailingSlash makes it match with
  // zero nginx changes. See docs/nextjs-migration-findings.md for the full reasoning.
  trailingSlash: true,
  ...(process.env.NODE_ENV === 'development'
    ? {
        // `output: 'export'` forbids rewrites at `next build` time, but `next dev` never runs that
        // build-time check, so this only ever applies to `npm run dev`. Mirrors the old
        // vite.config.ts proxy: `api` resolves inside the docker-compose network.
        async rewrites() {
          return [{ source: '/api/:path*', destination: 'http://api:8000/api/:path*' }]
        },
      }
    : {}),
}

export default nextConfig
