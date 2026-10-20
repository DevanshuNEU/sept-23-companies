import { expect, test } from './fixtures';

test('2 switches to People and the URL is /no-go/people', async ({ page, consoleErrors }) => {
  await page.goto('/no-go');
  await expect(page.getByRole('tab', { name: /^Companies/ })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('table', { name: 'No-go companies' })).toBeVisible();

  await page.keyboard.press('2');
  await expect(page).toHaveURL(/\/no-go\/people$/);
  await expect(page.getByRole('tab', { name: /^People/ })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('row', { name: /Ruth Okonkwo/ })).toContainText('Asked to stop');

  await page.keyboard.press('1');
  await expect(page).toHaveURL(/\/no-go$/);
  expect(consoleErrors).toEqual([]);
});
