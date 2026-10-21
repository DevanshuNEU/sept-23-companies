import type { Page } from '@playwright/test';
import { expect, test } from './fixtures';

// The phase-1 Today, now at /proposals: its own capacity box. The sidebar count reads the plan (2 of 7), not the picks.
const picked = (page: Page) => page.getByLabel('Capacity').locator('div').filter({ hasText: /^Picked today/ });

async function openProposals(page: Page) {
  await page.goto('/proposals');
  await expect(page.getByRole('heading', { name: 'Tuesday, November 10' })).toBeVisible();
  await expect(picked(page)).toContainText('3 of 5');
}

test('j selects Pylon, x picks it, and Picked today reads 4 of 5 at once', async ({ page, consoleErrors }) => {
  await openProposals(page);
  // Nothing is selected on load, so the first j lands on the first row (Pylon). A second j would land on
  // Resend, which is already picked, and x there would unpick it.
  await page.keyboard.press('j');
  await expect(page.getByRole('complementary', { name: 'Pylon preview' })).toBeVisible();
  await page.keyboard.press('x');
  await expect(picked(page)).toContainText('4 of 5');
  await expect(page.getByText('Picked Pylon')).toBeVisible();
  await expect(page.getByRole('checkbox', { name: 'Pick Pylon' })).toBeChecked();
  expect(consoleErrors).toEqual([]);
});

test('at capacity, hovering an unpicked checkbox shows the limit and x picks nothing', async ({ page, consoleErrors }) => {
  await openProposals(page);
  await page.keyboard.press('j');
  await expect(page.getByRole('complementary', { name: 'Pylon preview' })).toBeVisible();
  await page.keyboard.press('x');
  await expect(picked(page)).toContainText('4 of 5');

  await page.getByRole('checkbox', { name: 'Pick Baseten' }).locator('xpath=ancestor::tr').click();
  await expect(page.getByRole('complementary', { name: 'Baseten preview' })).toBeVisible();
  await page.keyboard.press('x');
  await expect(picked(page)).toContainText('5 of 5');

  const inngest = page.getByRole('checkbox', { name: 'Pick Inngest' });
  await expect(inngest).toHaveAttribute('aria-disabled', 'true');
  await inngest.locator('xpath=ancestor::tr').click();
  // Wait for the selection to commit: an x pressed early lands on Baseten and unpicks it.
  await expect(page.getByRole('complementary', { name: 'Inngest preview' })).toBeVisible();
  await page.keyboard.press('x');
  await expect(inngest).not.toBeChecked();
  await expect(picked(page)).toContainText('5 of 5');

  await inngest.hover();
  await expect(page.getByRole('tooltip')).toContainText("Today's limit carries 5 companies. Unpick one first.");
  expect(consoleErrors).toEqual([]);
});
