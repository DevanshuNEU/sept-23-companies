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

test('a blocked draft explains itself on Approve', async ({ page, consoleErrors }) => {
  await page.goto('/outbox/2002');
  const approve = page.getByRole('button', { name: 'Approve' });
  await expect(approve).toHaveAttribute('aria-disabled', 'true');
  await approve.hover();
  await expect(page.getByRole('tooltip')).toContainText("Blocked: near-identical to Ava Moreno's draft. Edit, then approve.");
  expect(consoleErrors).toEqual([]);
});

test('a approves a clean draft with Undo, and z takes it back', async ({ page, consoleErrors }) => {
  await page.goto('/outbox/2006');
  await expect(page.getByRole('heading', { level: 2, name: 'Omar Haddad' })).toBeVisible();
  await page.keyboard.press('a');
  await expect(page.getByText('Approved Omar Haddad. Sends after 10:55 ET.')).toBeVisible();
  await expect(page.getByRole('option', { selected: true })).toContainText('Approved');
  await page.keyboard.press('z');
  await expect(page.getByRole('option', { selected: true })).toContainText('Draft');
  await expect(page.getByRole('button', { name: 'Approve' })).toBeVisible();
  expect(consoleErrors).toEqual([]);
});

test('a reply shows the hold alert', async ({ page, consoleErrors }) => {
  await page.goto('/outbox/2001');
  await expect(page.getByText('Browserbase is on hold')).toBeVisible();
  await expect(page.getByRole('link', { name: /Open in Gmail/ })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Approve' })).toHaveCount(0);
  expect(consoleErrors).toEqual([]);
});
