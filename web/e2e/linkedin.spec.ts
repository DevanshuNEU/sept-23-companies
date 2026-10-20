import { expect, test } from './fixtures';

test('r then z leaves Hannah Cole at Not started', async ({ page, consoleErrors }) => {
  await page.goto('/linkedin');
  const hannah = page.getByRole('row').filter({ hasText: 'Hannah Cole' });
  await expect(hannah).toContainText('Not started');

  await hannah.click();
  // The click writes ?sel= through a router transition; a key pressed before it commits finds nothing selected.
  await expect(hannah).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('r');
  await expect(page.getByText('Logged: request sent to Hannah Cole.')).toBeVisible();
  await expect(hannah).toContainText('Requested today');

  await page.keyboard.press('z');
  await expect(hannah).toContainText('Not started');
  await expect(page.getByRole('tab', { name: /^To reach/ })).toContainText('12');
  expect(consoleErrors).toEqual([]);
});
