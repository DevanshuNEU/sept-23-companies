import { expect, test } from './fixtures';

test('app loads on 5173 with no console errors', async ({ page, consoleErrors }) => {
  await page.goto('/');
  await expect(page.getByText('Outreach')).toBeVisible();
  expect(consoleErrors).toEqual([]);
});
