import { expect, test } from './fixtures';

// Browse companies (#120, F), on the `queue` sample: every company the plan could pick, paged, with a name search and
// three filters. "Use instead of..." swaps one of today's picks for the company: not a Not now, no room used.
test('Browse companies: search, filters, paging, a refused company and a swap by keyboard', async ({ page, consoleErrors }) => {
  await page.clock.install({ time: new Date('2026-11-10T15:14:00Z') });
  await page.goto('/today?scenario=queue');
  const rail = page.getByRole('listbox', { name: "Today's picks" });
  await expect(rail.getByRole('option')).toHaveCount(8);

  await page.locator('body').press('f');
  const drawer = page.getByRole('dialog', { name: /Browse companies/ });
  const rows = drawer.getByRole('list', { name: 'Companies' }).getByRole('listitem');
  await expect(rows).toHaveCount(25);
  await expect(rows.first()).toContainText('Retell AI');
  await expect(drawer).not.toContainText('Pylon'); // on today's list
  await expect(drawer).not.toContainText('Warp'); // in flight: never listed

  // Paging: 25 a page, no repeats.
  await drawer.getByRole('button', { name: 'Show more' }).click();
  await expect(rows).toHaveCount(42);
  await expect(drawer.getByRole('button', { name: 'Show more' })).toHaveCount(0);

  // Search by name key, then a filter chip.
  const search = drawer.getByRole('searchbox', { name: 'Search companies by name' });
  await search.fill('retell');
  await expect(rows).toHaveCount(1);
  await search.fill('...');
  await expect(drawer).toContainText('No company matches');
  await search.fill('');
  await expect(rows).toHaveCount(42); // the pages he already loaded are kept
  const all = await drawer.getByText(/\d+ companies, best ranked first/).textContent();
  for (const chip of ['Role at my level', 'Visa not a no', 'Not worked yet']) {
    await drawer.getByRole('button', { name: chip }).click();
    await expect(drawer.getByRole('button', { name: chip })).toHaveAttribute('aria-pressed', 'true');
    await expect(drawer.getByText(/\d+ companies, best ranked first/)).not.toHaveText(all ?? '');
    await drawer.getByRole('button', { name: chip }).click();
    await expect(drawer.getByText(/\d+ companies, best ranked first/)).toHaveText(all ?? '');
  }

  // A company held back: listed with its reason, no button.
  await search.fill('cinder');
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText('You said Not now to Cinder on Nov 2.');
  await expect(rows.first().getByRole('button', { name: /Use instead of/ })).toHaveCount(0);
  await search.fill('');
  await expect(rows).toHaveCount(42);

  // The keys: arrows move, Enter opens Use instead of..., Escape steps back, a digit swaps.
  await search.press('ArrowDown');
  await expect(rows.nth(1)).toHaveAttribute('aria-current', 'true');
  await search.press('ArrowUp');
  await expect(rows.first()).toHaveAttribute('aria-current', 'true');
  await search.press('Enter');
  const chooser = drawer.getByRole('group', { name: 'Use Retell AI instead of' });
  await expect(chooser).toContainText('It is not a Not now, and it uses no room.');
  await expect(chooser.getByRole('button')).toHaveCount(6); // Resend and Baseten were reached
  await page.keyboard.press('Escape');
  await expect(chooser).toHaveCount(0);
  await expect(drawer).toBeVisible();
  await page.keyboard.press('Enter');
  await expect(chooser).toBeVisible();
  await page.keyboard.press('1');
  await expect(page.getByText('Swapped in Retell AI for Pylon.')).toBeVisible();
  await expect(page.getByRole('dialog', { name: /Browse companies/ })).toHaveCount(0);
  await expect(rail.getByRole('option')).toHaveCount(8);
  await expect(rail).toContainText('Retell AI');
  await expect(rail).not.toContainText('Pylon');
  await page.clock.runFor(9000); // the undo window closes and the swap commits
  await expect(page.getByText('8 picks', { exact: true })).toBeVisible(); // no "passed" count: it is not a Not now

  // The replaced pick is back on More options, with no cooldown.
  await page.locator('body').press('b');
  const bench = page.getByRole('dialog', { name: /More options/ });
  await expect(bench.getByRole('list', { name: 'More options' })).toContainText('Pylon');
  expect(consoleErrors).toEqual([]);
});
