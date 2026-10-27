import { expect, test } from './fixtures';

// Browse companies (#120), the full page at /browse, on the `queue` sample: every company the plan could pick, in one
// labelled table, with a name search and three filters. "Use instead of..." swaps one of today's picks for the
// company: not a Not now, no room used.
test('Browse companies: the table, search, filters, a refused company and a swap by keyboard', async ({ page, consoleErrors }) => {
  await page.clock.install({ time: new Date('2026-11-10T15:14:00Z') });
  await page.goto('/today?scenario=queue');
  const rail = page.getByRole('listbox', { name: "Today's picks" });
  await expect(rail.getByRole('option')).toHaveCount(8);
  await expect(page.getByRole('button', { name: /^Browse companies/ })).toHaveCount(0); // no button in the rail

  // The sidebar link opens the page; F on Today does the same.
  await page.getByRole('link', { name: 'Browse companies' }).click();
  await expect(page).toHaveURL(/\/browse/);
  await expect(page.getByRole('heading', { level: 1, name: 'Browse companies' })).toBeVisible();
  await page.goBack();
  await expect(rail.getByRole('option')).toHaveCount(8);
  await page.locator('body').press('f');
  await expect(page).toHaveURL(/\/browse/);

  const table = page.getByRole('table', { name: 'Companies' });
  const rows = table.locator('tbody tr[data-browse]');
  await expect(table.getByRole('columnheader')).toHaveText(['Rank', 'Company', 'Fit', 'Why it ranked', 'Open role', 'Size and stage', 'Visa', 'Email', 'Research', 'Action']);
  await expect(rows).toHaveCount(42);
  await expect(page.getByText('Showing 42 of 42 companies')).toBeVisible();
  await expect(rows.first()).toContainText('Retell AI');
  await expect(table).not.toContainText('Pylon'); // on today's list
  await expect(table).not.toContainText('Warp'); // in flight: never listed

  // Search by name key, then each filter chip.
  const search = page.getByRole('searchbox', { name: 'Search companies by name' });
  await search.fill('retell');
  await expect(rows).toHaveCount(1);
  await search.fill('...');
  await expect(page.getByText('No company matches')).toBeVisible();
  await search.fill('');
  await expect(rows).toHaveCount(42);
  for (const chip of ['Role at my level', 'Visa not a no', 'Not worked yet']) {
    await page.getByRole('button', { name: chip }).click();
    await expect(page.getByRole('button', { name: chip })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByText('Showing 42 of 42 companies')).toHaveCount(0);
    await page.getByRole('button', { name: chip }).click();
    await expect(page.getByText('Showing 42 of 42 companies')).toBeVisible();
  }

  // A company held back: listed with its reason, no button.
  await search.fill('cinder');
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText('You said Not now to Cinder on Nov 2.');
  await expect(rows.first().getByRole('button', { name: /Use instead of/ })).toHaveCount(0);
  await search.fill('');
  await expect(rows).toHaveCount(42);

  // The keys: arrows move, Enter opens Use instead of..., Escape closes it, a digit swaps.
  await search.press('ArrowDown');
  await expect(rows.nth(1)).toHaveAttribute('aria-current', 'true');
  await search.press('ArrowUp');
  await expect(rows.first()).toHaveAttribute('aria-current', 'true');
  await search.press('Enter');
  const chooser = page.getByRole('group', { name: 'Use Retell AI instead of' });
  await expect(chooser).toContainText('It is not a Not now, and it uses no room.');
  await expect(chooser.getByRole('button')).toHaveCount(6); // Resend and Baseten were reached
  await page.keyboard.press('Escape');
  await expect(chooser).toHaveCount(0);
  await expect(page).toHaveURL(/\/browse/); // Escape closed the list, it did not leave the page
  await page.keyboard.press('Enter');
  await expect(chooser).toBeVisible();
  await page.keyboard.press('1');
  await expect(page.getByText('Swapped in Retell AI for Pylon.')).toBeVisible();
  await expect(rows.filter({ hasText: 'Retell AI' })).toHaveCount(0); // it is on today's list now
  await page.clock.runFor(9000); // the undo window closes and the swap commits
  await expect(rows.filter({ hasText: 'Pylon' })).toHaveCount(1); // the replaced pick is back in the list

  // On Today: the swapped company is in the rail, no pass counted.
  await page.getByRole('link', { name: /^Today/ }).click();
  await expect(rail.getByRole('option')).toHaveCount(8);
  await expect(rail).toContainText('Retell AI');
  await expect(rail).not.toContainText('Pylon');
  await expect(page.getByText('8 picks', { exact: true })).toBeVisible();
  expect(consoleErrors).toEqual([]);
});
