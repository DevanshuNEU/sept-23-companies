import { expect, test } from './fixtures';

test('mod+k, type brow, Enter opens the Browserbase company page', async ({ page, consoleErrors }) => {
  await page.goto('/today');
  await expect(page.getByRole('heading', { level: 1, name: 'Today' })).toBeVisible();
  await page.keyboard.press('ControlOrMeta+k');
  await page.keyboard.type('brow');
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { level: 1, name: 'Browserbase' })).toBeVisible();
  await expect(page.getByRole('group', { name: 'Status' }).getByText('Held: reply')).toBeVisible();
  expect(consoleErrors).toEqual([]);
});
