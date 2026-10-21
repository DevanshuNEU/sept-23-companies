import { expect, test } from './fixtures';

test('g then d from Today lands on Done', async ({ page, consoleErrors }) => {
  await page.goto('/today');
  await expect(page.getByRole('heading', { level: 1, name: 'Today' })).toBeVisible();
  await page.keyboard.press('g');
  await page.keyboard.press('d');
  await expect(page).toHaveURL(/\/done$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Done companies' })).toBeVisible();
  await expect(page.getByRole('img', { name: 'Replies per week, last 8 weeks: 0, 1, 2, 1, 3, 4, 5, 4 so far' })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'Crusoe', exact: true })).toBeVisible();
  expect(consoleErrors).toEqual([]);
});
