import { expect, test } from './fixtures';

test('g p and g w reach Progress and Wins', async ({ page, consoleErrors }) => {
  await page.goto('/browse');
  await expect(page.getByRole('heading', { level: 1, name: 'Jobs' })).toBeVisible();
  const sidebar = page.locator('[data-slot="sidebar"]');

  await page.keyboard.press('g');
  await page.keyboard.press('p');
  await expect(page).toHaveURL(/\/progress$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Progress' })).toBeAttached();
  await expect(sidebar.getByRole('link', { name: 'Progress' })).toHaveAttribute('aria-current', 'page');

  await page.keyboard.press('g');
  await page.keyboard.press('w');
  await expect(page).toHaveURL(/\/wins$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Wins' })).toBeAttached();
  expect(consoleErrors).toEqual([]);
});

test('a new win shows as Wins, 1 new in the sidebar', async ({ page, consoleErrors }) => {
  await page.goto('/browse?scenario=win');
  await expect(page.locator('[data-slot="sidebar"]').getByRole('link', { name: 'Wins, 1 new' })).toBeVisible();
  expect(consoleErrors).toEqual([]);
});
