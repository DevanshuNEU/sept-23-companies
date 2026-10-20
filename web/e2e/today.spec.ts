import type { Page } from '@playwright/test';
import { expect, test } from './fixtures';

const todayLink = (page: Page) => page.locator('[data-slot="sidebar"]').getByRole('link', { name: /^Today/ });

async function openToday(page: Page) {
  await page.goto('/today');
  await expect(page.getByRole('heading', { name: 'Tuesday, November 10' })).toBeVisible();
  await expect(todayLink(page)).toContainText('3 of 5');
}

test('j selects Pylon, x picks it, and the sidebar reads 4 of 5 at once', async ({ page, consoleErrors }) => {
  await openToday(page);
  // Nothing is selected on load, so the first j lands on the first row (Pylon). A second j would land on
  // Resend, which is already picked, and x there would unpick it.
  await page.keyboard.press('j');
  await expect(page.getByRole('complementary', { name: 'Pylon preview' })).toBeVisible();
  await page.keyboard.press('x');
  await expect(todayLink(page)).toContainText('4 of 5');
  await expect(page.getByText('Picked Pylon')).toBeVisible();
  await expect(page.getByRole('checkbox', { name: 'Pick Pylon' })).toBeChecked();
  expect(consoleErrors).toEqual([]);
});

test('at capacity, hovering an unpicked checkbox shows the limit and x picks nothing', async ({ page, consoleErrors }) => {
  await openToday(page);
  await page.keyboard.press('j');
  await page.keyboard.press('x');
  await expect(todayLink(page)).toContainText('4 of 5');

  await page.getByRole('checkbox', { name: 'Pick Baseten' }).locator('xpath=ancestor::tr').click();
  await expect(page.getByRole('complementary', { name: 'Baseten preview' })).toBeVisible();
  await page.keyboard.press('x');
  await expect(todayLink(page)).toContainText('5 of 5');

  const inngest = page.getByRole('checkbox', { name: 'Pick Inngest' });
  await expect(inngest).toHaveAttribute('aria-disabled', 'true');
  await inngest.locator('xpath=ancestor::tr').click();
  await page.keyboard.press('x');
  await expect(inngest).not.toBeChecked();
  await expect(todayLink(page)).toContainText('5 of 5');

  await inngest.hover();
  await expect(page.getByRole('tooltip')).toContainText("Today's limit carries 5 companies. Unpick one first.");
  expect(consoleErrors).toEqual([]);
});
