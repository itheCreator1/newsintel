import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  use: {
    baseURL: process.env.NEWSINTEL_E2E_BASE_URL || 'http://127.0.0.1:18081',
    browserName: 'chromium',
    headless: true,
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || '/usr/bin/chromium' },
  },
})
