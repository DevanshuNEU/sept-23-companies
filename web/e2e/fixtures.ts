import { test as base, expect } from '@playwright/test';

type Fixtures = {
  consoleErrors: string[];
};

export const test = base.extend<Fixtures>({
  consoleErrors: async ({ page }, use) => {
    const errors: string[] = [];
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text());
    });
    page.on('pageerror', (error) => {
      errors.push(error.message);
    });
    await use(errors);
  },
  page: async ({ page }, use) => {
    await page.clock.setFixedTime(new Date('2026-11-10T15:14:00Z'));
    await use(page);
  },
});

export { expect };
