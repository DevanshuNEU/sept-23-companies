import { expect, test } from './fixtures';

// Today as the top picks (#9 company-first ruling, P2; top picks 2026-10-26), on the `queue` sample: 8 picks, new
// openings first, 2 reached, Pylon first to do. He finds the people on LinkedIn; the page never reaches LinkedIn itself.
test('the company queue: facts, the search link, a pasted log, a saved search and In flight', async ({ page, consoleErrors }) => {
  const foreign: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.protocol === 'http:' && url.hostname !== '127.0.0.1') foreign.push(request.url());
    if (url.protocol === 'https:') foreign.push(request.url());
  });
  await page.clock.install({ time: new Date('2026-11-10T15:14:00Z') });
  await page.goto('/today?scenario=queue');

  await expect(page.getByText('8 picks, 2 reached')).toBeVisible();
  const rail = page.getByRole('listbox', { name: "Today's picks" });
  await expect(rail.getByRole('option')).toHaveCount(8);
  await expect(rail.getByRole('option').first()).toContainText('New opening, 6 hours ago');
  const panel = page.getByRole('region', { name: 'Selected company' });
  await expect(panel.getByRole('heading', { level: 2, name: 'Pylon' })).toBeVisible();
  const why = panel.getByRole('region', { name: 'Why today' });
  await expect(why).toContainText('SWE I role, first seen 6 hours ago');
  await expect(rail.getByRole('option').first()).toContainText('SWE I role: Software Engineer I');
  await expect(why).toContainText('30 people (estimate), Series A');
  await expect(why).toContainText('3 new engineering roles in the last 30 days');
  await expect(why).toContainText('Says it sponsors, E-Verify enrolled');
  await expect(panel.getByRole('link', { name: 'LinkedIn page' })).toHaveAttribute('href', /^https:\/\/www\.linkedin\.com\/search\/results\/companies\/\?keywords=Pylon$/);

  const search = panel.getByRole('link', { name: 'Open LinkedIn search' });
  await expect(search).toHaveAttribute('href', /^https:\/\/www\.linkedin\.com\/search\/results\/people\/\?currentCompany=/);
  await expect(search).toHaveAttribute('target', '_blank');
  await expect(search).toHaveAttribute('rel', /noopener/);
  await expect(panel.getByRole('link', { name: 'Careers' })).toHaveAttribute('target', '_blank');

  // L puts the cursor in Log people; Trigger.dev has no role, so the paste finishes it and the selection moves on.
  await rail.getByRole('option', { name: /Trigger\.dev/ }).click();
  await expect(panel.getByRole('heading', { level: 2, name: 'Trigger.dev' })).toBeVisible();
  await page.locator('body').press('l');
  const box = panel.getByRole('textbox', { name: /Log people you asked/ });
  await expect(box).toBeFocused();
  await box.fill('Jane Doe | Senior Software Engineer | Hi Jane\nOmar Diaz | Engineering Manager');
  await box.press('ControlOrMeta+Enter');
  await expect(page.getByText('Logged: 2 people at Trigger.dev.')).toBeVisible();
  await expect(rail.getByRole('option', { name: '7. Trigger.dev, Done' })).toBeVisible();
  await expect(page.getByText('8 picks, 3 reached')).toBeVisible();
  await expect(panel.getByRole('heading', { level: 2, name: 'Hex' })).toBeVisible();
  await page.clock.runFor(9000); // the undo window closes and the log commits

  // Save this search on Hex: S opens it from now on.
  const url = 'https://www.linkedin.com/search/results/people/?currentCompany=%5B%2277%22%5D&origin=FACETED_SEARCH';
  await panel.getByRole('textbox', { name: 'Save this search' }).fill(url);
  await panel.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Saved the search for Hex. S opens it.')).toBeVisible();
  await expect(panel.getByRole('link', { name: 'Open LinkedIn search' })).toHaveAttribute('href', url);

  // In flight is collapsed under the queue.
  const toggle = page.getByRole('button', { name: /In flight/ });
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await toggle.click();
  const flight = page.getByRole('list', { name: 'Companies in flight' });
  await expect(flight.getByRole('listitem').first()).toHaveText('BrowserbaseSomeone replied');
  await expect(flight).toContainText('Warp4 asked 6 days ago, free again Nov 18');

  await expect(page.getByRole('button', { name: /^Send/i })).toHaveCount(0);
  expect(consoleErrors).toEqual([]);
  expect(foreign).toEqual([]);
});

// Not now and the bench (top picks, 2026-10-26): a cooldown, never a no-go; the bench's best company takes the place.
test('Not now slides in the next best company, and More options adds one to today', async ({ page, consoleErrors }) => {
  await page.clock.install({ time: new Date('2026-11-10T15:14:00Z') });
  await page.goto('/today?scenario=queue');
  const rail = page.getByRole('listbox', { name: "Today's picks" });
  const panel = page.getByRole('region', { name: 'Selected company' });
  await expect(panel.getByRole('heading', { level: 2, name: 'Pylon' })).toBeVisible();

  await page.locator('body').press('p');
  const chips = panel.getByRole('group', { name: 'Not now for Pylon' });
  await expect(chips).toContainText('It is not a no-go.');
  await page.locator('body').press('2');
  await expect(page.getByText('Passed. Added Retell AI.')).toBeVisible();
  await expect(rail.getByRole('option').first()).toContainText('Retell AI');
  await expect(rail).not.toContainText('Pylon');
  await expect(panel.getByRole('heading', { level: 2, name: 'Cerebrium' })).toBeVisible();
  await page.clock.runFor(9000); // the undo window closes and the pass commits
  await expect(rail.getByRole('option').first()).toContainText('New opening, 3 hours ago');
  await expect(page.getByText('8 picks, 2 reached, 1 passed')).toBeVisible();

  await page.locator('body').press('b');
  const bench = page.getByRole('dialog', { name: /More options/ });
  await expect(bench.getByRole('listitem').first()).toContainText('Railway');
  await expect(bench).toContainText('You applied on Oct 20; this is a new role.');
  await expect(bench).not.toContainText('Crusoe');
  await bench.getByRole('listitem').filter({ hasText: 'Graphite' }).getByRole('button', { name: 'Add to today' }).click();
  await expect(page.getByText('Added Graphite to today.')).toBeVisible();
  await expect(rail.getByRole('option')).toHaveCount(9);
  await expect(page.getByRole('dialog', { name: /More options/ })).toHaveCount(0);
  expect(consoleErrors).toEqual([]);
});
