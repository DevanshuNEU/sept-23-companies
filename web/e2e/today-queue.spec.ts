import { expect, test } from './fixtures';

// Today as the top picks (#9 company-first ruling, P2; top picks 2026-10-26; the Today redesign), on the `queue` sample: 8
// picks, new openings first, 2 reached, Pylon first to do, with the company's profile in a third column from 1280 px. He finds the people on LinkedIn; the page never reaches LinkedIn itself.
test('the company queue: facts, the search link, a pasted log, a saved search and In flight', async ({ page, consoleErrors }) => {
  const foreign: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.protocol === 'http:' && url.hostname !== '127.0.0.1') foreign.push(request.url());
    if (url.protocol === 'https:') foreign.push(request.url());
  });
  await page.clock.install({ time: new Date('2026-11-10T15:14:00Z') });
  await page.goto('/today?scenario=queue');

  await expect(page.getByText('8 picks', { exact: true })).toBeVisible();
  await expect(page.getByRole('meter', { name: 'Picks reached today' })).toHaveAttribute('aria-valuetext', '2 of 8 reached');
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
  await expect(why.getByRole('list', { name: 'Why it should answer' })).toContainText('Says it sponsors');
  await expect(why.getByRole('list', { name: 'Why it should answer' })).toContainText('E-Verify enrolled');
  await expect(why).toContainText('Agent work on support tickets is close to the work he wants.');
  // The profile is the third column at this width (1280): what does not change, each fact with its source.
  const profile = page.getByRole('complementary', { name: 'Company profile' });
  await expect(profile).toContainText('confirmed, SEC Form D');
  await expect(profile).toContainText('4 software LCA filings in FY2024 and FY2025');
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
  await expect(page.getByRole('meter', { name: 'Picks reached today' })).toHaveAttribute('aria-valuetext', '3 of 8 reached');
  await expect(profile).toContainText('Not known');
  await expect(panel.getByRole('heading', { level: 2, name: 'Hex' })).toBeVisible();
  await page.clock.runFor(9000); // the undo window closes and the log commits

  // Save this search on Hex (a closed row until he opens it): S opens it from now on.
  await panel.getByRole('button', { name: /Save this search/ }).click();
  const url = 'https://www.linkedin.com/search/results/people/?currentCompany=%5B%2277%22%5D&origin=FACETED_SEARCH';
  await panel.getByRole('textbox', { name: 'Save this search' }).fill(url);
  await panel.getByRole('button', { name: 'Save', exact: true }).click();
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

// Flag a bad pick (2026-10-28): Wrong company and Wrong location are chips, and Other asks for his own words first.
test('Not now: Wrong location is a chip, and Other takes his own words', async ({ page, consoleErrors }) => {
  await page.clock.install({ time: new Date('2026-11-10T15:14:00Z') });
  await page.goto('/today?scenario=queue');
  const panel = page.getByRole('region', { name: 'Selected company' });
  await expect(panel.getByRole('heading', { level: 2, name: 'Pylon' })).toBeVisible();

  await page.locator('body').press('p');
  const chips = panel.getByRole('group', { name: 'Not now for Pylon' });
  await expect(chips.getByRole('button', { name: /Wrong company/ })).toBeVisible();
  await expect(chips.getByRole('button', { name: /Wrong location/ })).toBeVisible();
  await chips.screenshot({ path: '../.context/not-now-chips.png' });

  await page.locator('body').press('8');
  const why = chips.getByRole('textbox', { name: 'Why' });
  await expect(why).toBeFocused();
  await why.fill('Based in Brazil, I cannot apply');
  await page.screenshot({ path: '../.context/not-now-other.png' });
  await why.press('Enter');
  await expect(page.getByText('Passed. Added Retell AI.')).toBeVisible();
  await page.clock.runFor(9000);
  await expect(page.getByText('8 picks, 1 passed')).toBeVisible();
  expect(consoleErrors).toEqual([]);
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
  await expect(page.getByText('8 picks, 1 passed')).toBeVisible();
  await expect(page.getByRole('meter', { name: 'Picks reached today' })).toHaveAttribute('aria-valuetext', '2 of 8 reached');

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

// The profile folds under the company below 1280 px, closed, and opens to the same facts (nothing is lost).
test('under 1280 px the profile is a collapsed section under the company', async ({ page, consoleErrors }) => {
  await page.setViewportSize({ width: 1150, height: 800 });
  await page.clock.install({ time: new Date('2026-11-10T15:14:00Z') });
  await page.goto('/today?scenario=queue');
  const panel = page.getByRole('region', { name: 'Selected company' });
  await expect(panel.getByRole('heading', { level: 2, name: 'Pylon' })).toBeVisible();
  await expect(page.getByRole('complementary', { name: 'Company profile' })).toHaveCount(0);
  const fold = panel.getByRole('button', { name: /Company profile/ });
  await expect(fold).toHaveAttribute('aria-expanded', 'false');
  await expect(fold).toContainText('Series A, 30 people, San Francisco, CA');
  await fold.click();
  await expect(panel.getByRole('heading', { level: 3, name: 'Visa' })).toBeVisible();
  await expect(panel).toContainText('4 software LCA filings in FY2024 and FY2025');
  expect(consoleErrors).toEqual([]);
});
