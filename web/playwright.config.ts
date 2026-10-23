import { defineConfig } from '@playwright/test';

// WEB_PORT lets each Conductor workspace run its own e2e server (CONDUCTOR_PORT); 5173 stays the default.
// Never 3001, 3002, 3011 or 3013: those belong to the live mail system and the old dashboard preview.
const port = process.env.WEB_PORT ?? '5173';

// Local runs drive the installed Google Chrome. PW_BUNDLED=1 (CI) drops that and uses the Chromium Playwright installed.
const bundled = Boolean(process.env.PW_BUNDLED);
const ci = Boolean(process.env.CI);

export default defineConfig({
  testDir: 'e2e',
  workers: 1,
  forbidOnly: ci,
  retries: 0,
  reporter: ci ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    ...(bundled ? {} : { channel: 'chrome' as const }),
    timezoneId: 'America/New_York',
    locale: 'en-US',
  },
  webServer: {
    command: `pnpm dev --host 127.0.0.1 --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
  },
});
