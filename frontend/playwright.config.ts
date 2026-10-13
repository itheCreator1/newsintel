import { chromium, defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  outputDir: process.env.NEWSINTEL_E2E_OUTPUT_DIR || 'test-results',
  timeout: 60_000,
  // The gates in CI retry a failed test once (NEWSINTEL_E2E_RETRIES=1); one that passes on the retry
  // is reported as flaky by infra/test-e2e.sh instead of failing the run. Local runs never retry.
  retries: Number(process.env.NEWSINTEL_E2E_RETRIES || 0),
  use: {
    baseURL: process.env.NEWSINTEL_E2E_BASE_URL || 'http://127.0.0.1:18081',
    browserName: 'chromium',
    headless: true,
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || chromium.executablePath() },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
})
