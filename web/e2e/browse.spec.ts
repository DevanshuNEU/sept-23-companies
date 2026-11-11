import { expect, test } from './fixtures';

// Jobs (#156), the full page at /browse: open engineering postings, newest first, in two tabs. He reads each one himself:
// the arrows move, Enter opens the posting, X passes and A marks the company done (reached). No score and no ranking text.
test('Jobs: Knock opens on it; the list, the tabs and filters, then pass and done by keyboard', async ({ page, consoleErrors }) => {
  await page.clock.install({ time: new Date('2026-11-10T15:14:00Z') });
  // Knock opens on the job list (#195), and the old Today address lands there too.
  await page.goto('/today?scenario=queue');
  await expect(page).toHaveURL(/\/browse\?scenario=queue$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Jobs' })).toBeVisible();
  const sidebar = page.locator('[data-slot="sidebar"]');
  await expect(sidebar.getByRole('navigation', { name: 'Views' }).getByRole('link').first()).toHaveText('Jobs');
  await expect(sidebar.getByRole('link', { name: /^Today/ })).toHaveCount(0);
  // Today's outreach under the header, opening Progress.
  await expect(page.getByRole('link', { name: /^Today: \d+ sent, \d+ repl(y|ies)$/ })).toHaveAttribute('href', '/progress');

  const jobs = page.getByRole('list', { name: 'Jobs' }).locator('> li[data-job]');
  await expect(page.getByText('22 at your level in the last 30 days')).toBeVisible();
  await expect(jobs).toHaveCount(20);
  await expect(jobs.first()).toContainText('Software Engineer, New Grad');
  await expect(jobs.first()).toContainText('Retell AI');
  await expect(jobs.first()).toContainText('posted 10:08, 6 min ago');
  await expect(jobs.first().getByRole('link', { name: 'Software Engineer, New Grad' })).toHaveAttribute('target', '_blank');

  // The other tab, a window chip and a size.
  await page.getByRole('tab', { name: /^Higher levels/ }).click();
  await expect(page.getByText('6 at higher levels in the last 30 days')).toBeVisible();
  await page.getByRole('tab', { name: /^Your level/ }).click();
  await expect(jobs).toHaveCount(20);
  await page.getByRole('button', { name: '7 days' }).click();
  await expect(page.getByText('8 at your level in the last 7 days')).toBeVisible();
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
  await expect(page.getByText('21 at your level in the last 30 days')).toBeVisible();
  await expect(jobs.nth(1)).toHaveAttribute('aria-current', 'true');
  await expect(jobs.nth(1)).toHaveAttribute('data-job', next ?? '');

  // A marks the company done: every row of it says reached at once, with Undo; then the row says what happened.
  await page.keyboard.press('ArrowDown');
  await expect(jobs.nth(2)).toHaveAttribute('aria-current', 'true');
  const company = (await jobs.nth(2).getAttribute('data-company')) ?? '';
  const ofCompany = page.getByRole('list', { name: 'Jobs' }).locator(`> li[data-company="${company}"]`);
  await page.keyboard.press('a');
  await expect(page.getByText(`Marked ${company} reached.`)).toBeVisible();
  await expect(jobs.nth(2)).toContainText('Marking reached');
  for (const r of await ofCompany.all()) await expect(r).toContainText('Reached Nov 10');
  // Z takes it back inside the window.
  await page.keyboard.press('z');
  await expect(jobs.nth(2).getByRole('button', { name: /^Mark reached/ })).toBeVisible();
  await expect(jobs.nth(2)).not.toContainText(/Reached [A-Z][a-z]{2} \d/); // the tag, not the button
  await page.keyboard.press('a');
  await page.clock.runFor(9000); // the undo window closes and the write goes out
  await expect(jobs.nth(2)).toContainText(`Marked ${company} reached.`);
  for (const r of await ofCompany.all()) await expect(r).toContainText('Reached Nov 10');
  await expect(jobs).toHaveCount(19);

  // A company he already contacted says when, and Not contacted yet hides it.
  await page.getByPlaceholder('Search by company name').fill('cerebrium');
  await expect(jobs.first()).toHaveAttribute('data-company', 'Cerebrium');
  await expect(jobs.first()).toContainText('Reached Nov 3');

  // Nothing matches: the page says what to widen.
  await page.getByPlaceholder('Search by company name').fill('zzzz');
  await expect(page.getByText('No jobs at your level in the last 30 days.')).toBeVisible();
  await expect(page.getByText('Widen the window or clear the search.')).toBeVisible();
  expect(consoleErrors).toEqual([]);
});

// Check now (#194): C asks for a check; the line under the header says how it is going, every second, until it ends,
// and the jobs it found land at the top (he is at the top, on no row).
test('Jobs: Check now by keyboard shows the progress line, then the done line, and the new jobs at the top', async ({ page, consoleErrors }) => {
  await page.goto('/browse?scenario=queue');
  const head = page.locator('header').filter({ has: page.getByRole('heading', { level: 1, name: 'Jobs' }) }).first();
  const jobs = page.getByRole('list', { name: 'Jobs' }).locator('> li[data-job]');
  await expect(jobs).toHaveCount(20);
  await expect(jobs.first()).toHaveAttribute('data-job', '9000');
  await expect(head.getByRole('button', { name: /^Check now/ })).toHaveAttribute('aria-keyshortcuts', 'c');

  await page.locator('body').press('c');
  await expect(head.getByText('Checked 1,240 of 4,013 boards, 3 new')).toBeVisible();
  await expect(head.getByText('Checked 4,013 boards in 3 min 10 s. 6 new.')).toBeVisible({ timeout: 10_000 });
  await expect(jobs.first()).toHaveAttribute('data-job', '9100');
  await expect(page.getByRole('button', { name: /^\d+ new$/ })).toHaveCount(0);
  expect(consoleErrors).toEqual([]);
});
