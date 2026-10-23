import { defineConfig } from '@playwright/test';

// WEB_PORT lets each Conductor workspace run its own e2e server (CONDUCTOR_PORT); 5173 stays the default.
// Never 3001, 3002, 3011 or 3013: those belong to the live mail system and the old dashboard preview.
const port = process.env.WEB_PORT ?? '5173';

export default defineConfig({
  testDir: 'e2e',
  workers: 1,
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    channel: 'chrome',
    timezoneId: 'America/New_York',
    locale: 'en-US',
  },
  webServer: {
    command: `pnpm dev --host 127.0.0.1 --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    // The e2e runs on the sample data: no API is started for it, so no view is served live.
    env: { VITE_LIVE_VIEWS: 'none' },
  },
});
