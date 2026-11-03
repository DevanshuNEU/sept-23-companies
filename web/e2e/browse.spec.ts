import { expect, test } from './fixtures';

// Jobs (#156), the full page at /browse: open engineering postings, newest first, in two tabs. He reads each one himself:
// the arrows move, Enter opens the posting, X passes and A goes ahead. No score and no ranking text.
test('Jobs: the list, the tabs and filters, then pass and go ahead by keyboard', async ({ page, consoleErrors }) => {
  await page.clock.install({ time: new Date('2026-11-10T15:14:00Z') });
  await page.goto('/today?scenario=queue');
  const rail = page.getByRole('listbox', { name: "Today's picks" });
  await expect(rail.getByRole('option')).toHaveCount(8);

  // The sidebar link opens the page; F on Today does the same.
  await page.getByRole('link', { name: 'Jobs' }).click();
  await expect(page).toHaveURL(/\/browse/);
  await expect(page.getByRole('heading', { level: 1, name: 'Jobs' })).toBeVisible();
  await page.goBack();
  await expect(rail.getByRole('option')).toHaveCount(8);
  await page.locator('body').press('f');
  await expect(page).toHaveURL(/\/browse/);

  const jobs = page.getByRole('list', { name: 'Jobs' }).locator('> li[data-job]');
  await expect(page.getByText('20 at your level in the last 30 days')).toBeVisible();
  await expect(jobs).toHaveCount(20);
  await expect(jobs.first()).toContainText('Software Engineer, New Grad');
  await expect(jobs.first()).toContainText('Retell AI');
  await expect(jobs.first()).toContainText('posted today');
  await expect(jobs.first().getByRole('link', { name: 'Software Engineer, New Grad' })).toHaveAttribute('target', '_blank');

  // The other tab, a window chip and a size.
  await page.getByRole('tab', { name: /^Higher levels/ }).click();
  await expect(page.getByText('6 at higher levels in the last 30 days')).toBeVisible();
  await page.getByRole('tab', { name: /^Your level/ }).click();
  await expect(jobs).toHaveCount(20);
  await page.getByRole('button', { name: '7 days' }).click();
  await expect(page.getByText('6 at your level in the last 7 days')).toBeVisible();
  await page.getByRole('button', { name: '30 days' }).click();
  await expect(jobs).toHaveCount(20);
  await page.getByRole('button', { name: 'Not known' }).click();
  await expect(jobs.first()).toContainText('Size not known');
  await page.getByRole('button', { name: 'Any size' }).click();
  await expect(jobs).toHaveCount(20);

  // Arrows move; X passes the row he is on, and the next row is current.
  await page.keyboard.press('ArrowDown');
  await expect(jobs.nth(1)).toHaveAttribute('aria-current', 'true');
  const next = await jobs.nth(2).getAttribute('data-job');
  await page.keyboard.press('x');
  await expect(jobs).toHaveCount(19);
  await expect(page.getByText('Passed.', { exact: true })).toBeVisible();

  // Z takes the pass back inside the window: the row returns and nothing is sent.
  await page.keyboard.press('z');
  await expect(jobs).toHaveCount(20);
  await expect(jobs.nth(1)).toHaveAttribute('data-job', '9001');
  await page.keyboard.press('x');
  await expect(jobs).toHaveCount(19);
  await expect(page.getByText('19 at your level in the last 30 days')).toBeVisible();
  await expect(jobs.nth(1)).toHaveAttribute('aria-current', 'true');
  await expect(jobs.nth(1)).toHaveAttribute('data-job', next ?? '');

  // A goes ahead: the row keeps its place and says what happened, in the API's words.
  await page.keyboard.press('ArrowDown');
  await expect(jobs.nth(2)).toHaveAttribute('aria-current', 'true');
  await page.keyboard.press('a');
  await expect(page.getByText('Going ahead with Baseten.')).toBeVisible();
  await expect(jobs.nth(2)).toContainText('Going ahead');
  await page.clock.runFor(9000); // the undo window closes and both writes go out
  await expect(jobs.nth(2)).toContainText("Added Baseten to today's list.");
  await expect(jobs).toHaveCount(19);

  // A refusal is said, and the row keeps its buttons.
  await page.getByPlaceholder('Search by company name').fill('cerebrium');
  await expect(jobs.first()).toHaveAttribute('data-company', 'Cerebrium');
  await jobs.first().getByRole('button', { name: /^Go ahead/ }).click();
  await page.clock.runFor(9000); // the undo window closes
  await expect(page.getByText('You contacted Cerebrium and are waiting for an answer.')).toBeVisible();
  await expect(jobs.first().getByRole('button', { name: /^Go ahead/ })).toBeVisible();

  // Nothing matches: the page says what to widen.
  await page.getByPlaceholder('Search by company name').fill('zzzz');
  await expect(page.getByText('No jobs at your level in the last 30 days.')).toBeVisible();
  await expect(page.getByText('Widen the window or clear the search.')).toBeVisible();
  expect(consoleErrors).toEqual([]);
});
