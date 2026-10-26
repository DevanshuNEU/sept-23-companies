import { expect, test } from './fixtures';

test('the Outbox shows the sending strip and the drafts', async ({ page, consoleErrors }) => {
  await page.goto('/outbox');
  const strip = page.getByRole('region', { name: 'Sending today' });
  await expect(strip.getByText('Running')).toBeVisible();
  await expect(strip.getByText('20 of 30 sent')).toBeVisible();
  // One line; the limit and the ramp are behind Details.
  await strip.getByRole('button', { name: 'Details about sending today' }).click();
  await expect(page.getByRole('dialog', { name: 'Details about sending today' }).getByText('Level 2 of 9, 30 a day')).toBeVisible();
  await expect(page.getByText('Kenji Watanabe', { exact: true })).toBeVisible();
  expect(consoleErrors).toEqual([]);
});

test('arriving opens the first reply, and j moves the selection', async ({ page, consoleErrors }) => {
  await page.goto('/outbox');
  await expect(page.getByText('Kenji Watanabe', { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/outbox\/2001$/);
  // The URL changes before React commits the new selection; the next key reads the committed one.
  await expect(page.getByRole('option', { selected: true })).toContainText('Priya Natarajan');
  await page.keyboard.press('j');
  // Drafts he can approve come first; Kenji's blocked draft is under "Blocked, fix first".
  await expect(page).toHaveURL(/\/outbox\/2004$/);
  await expect(page.getByRole('option', { selected: true })).toContainText('Dana Whitcomb');
  await expect(page.getByText('Blocked, fix first (2)')).toBeVisible();
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
  const approve = page.getByRole('button', { name: 'Approve', exact: true });
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
  // Approving moves on to the next draft (Sara); Omar settles into Approved.
  await expect(page.getByRole('option', { selected: true })).toContainText('Sara Lindqvist');
  const omar = page.getByRole('option').filter({ hasText: 'Omar Haddad' });
  await expect(omar).toContainText('Approved');
  await page.keyboard.press('z');
  await expect(omar).toContainText('Draft');
  await expect(page.getByRole('button', { name: 'Approve', exact: true })).toBeVisible();
  expect(consoleErrors).toEqual([]);
});

test('a reply shows the hold alert', async ({ page, consoleErrors }) => {
  await page.goto('/outbox/2001');
  await expect(page.getByText('Browserbase is on hold')).toBeVisible();
  await expect(page.getByRole('link', { name: /Open in Gmail/ })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Approve', exact: true })).toHaveCount(0);
  expect(consoleErrors).toEqual([]);
});
