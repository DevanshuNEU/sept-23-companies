import { expect, test } from './fixtures';

test('app loads on 5173 with no console errors', async ({ page, consoleErrors }) => {
  await page.goto('/');
  await expect(page.getByText('Knock', { exact: true }).first()).toBeVisible();
  expect(consoleErrors).toEqual([]);
});

const views: Array<[path: string, heading: string]> = [
  ['/today', 'Today'],
  ['/outbox', 'Outbox'],
  ['/outbox/1', 'Outbox'],
  ['/linkedin', 'LinkedIn replies'],
  ['/needs-you', 'Needs you'],
  ['/no-go', 'No-go list'],
  ['/no-go/people', 'No-go list'],
  ['/companies/1', 'Company'],
  ['/done', 'Done'],
];

for (const [path, heading] of views) {
  test(`${path} loads inside the shell with no console errors`, async ({ page, consoleErrors }) => {
    await page.goto(path);
    await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
    await expect(page.getByText('Sample data. Nothing is saved.')).toBeVisible();
    expect(consoleErrors).toEqual([]);
  });
}

test('/ and unknown paths open Today and keep the query', async ({ page, consoleErrors }) => {
  await page.goto('/?scenario=empty');
  await expect(page).toHaveURL(/\/today\?scenario=empty$/);
  await page.goto('/nowhere');
  await expect(page).toHaveURL(/\/today$/);
  expect(consoleErrors).toEqual([]);
});

test('g then o moves to the Outbox', async ({ page }) => {
  await page.goto('/today');
  await expect(page.getByRole('heading', { level: 1, name: 'Today' })).toBeVisible();
  await page.keyboard.press('g');
  await page.keyboard.press('o');
  await expect(page).toHaveURL(/\/outbox/);
});
