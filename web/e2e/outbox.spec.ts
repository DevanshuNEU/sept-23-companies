import { expect, test } from './fixtures';

test('the Outbox shows the sending strip and the drafts', async ({ page, consoleErrors }) => {
  await page.goto('/outbox');
  const strip = page.getByRole('region', { name: 'Sending today' });
  await expect(strip.getByText('Running')).toBeVisible();
  await expect(strip.getByText('Level 2 of 9, 30 a day')).toBeVisible();
  await expect(page.getByText('Kenji Watanabe', { exact: true })).toBeVisible();
  expect(consoleErrors).toEqual([]);
});

test('j moves the selection', async ({ page, consoleErrors }) => {
  await page.goto('/outbox');
  await expect(page.getByText('Kenji Watanabe', { exact: true })).toBeVisible();
  await page.keyboard.press('j');
  await expect(page).toHaveURL(/\/outbox\/2001$/);
  await page.keyboard.press('j');
  await expect(page).toHaveURL(/\/outbox\/2002$/);
  await expect(page.getByRole('option', { selected: true })).toContainText('Kenji Watanabe');
  expect(consoleErrors).toEqual([]);
});

test('many sent emails: the Sent group renders only what is on screen', async ({ page, consoleErrors }) => {
  await page.goto('/outbox?scenario=many_sent');
  const sent = page.getByRole('listbox', { name: 'Sent' });
  await expect(sent.getByRole('option').first()).toBeAttached();
  const rendered = await sent.getByRole('option').count();
  expect(rendered).toBeGreaterThan(0);
  expect(rendered).toBeLessThan(100);
  expect(consoleErrors).toEqual([]);
});
