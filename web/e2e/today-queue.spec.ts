import { expect, test } from './fixtures';

// Today as a company queue (#9 company-first ruling, P2), on the `queue` sample: 8 companies, 2 reached, Pylon first to
// do. He finds the people on LinkedIn; the page never reaches LinkedIn itself.
test('the company queue: facts, the search link, a pasted log, a saved search and In flight', async ({ page, consoleErrors }) => {
  const foreign: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.protocol === 'http:' && url.hostname !== '127.0.0.1') foreign.push(request.url());
    if (url.protocol === 'https:') foreign.push(request.url());
  });
  await page.clock.install({ time: new Date('2026-11-10T15:14:00Z') });
  await page.goto('/today?scenario=queue');

  await expect(page.getByText('8 companies, 2 reached')).toBeVisible();
  const rail = page.getByRole('listbox', { name: "Today's companies" });
  await expect(rail.getByRole('option')).toHaveCount(8);
  const panel = page.getByRole('region', { name: 'Selected company' });
  await expect(panel.getByRole('heading', { level: 2, name: 'Pylon' })).toBeVisible();
  const about = panel.getByLabel('About Pylon');
  await expect(about).toContainText('30 people (estimate), Series A');
  await expect(about).toContainText('3 new engineering roles in the last 30 days');
  await expect(about).toContainText('Says it sponsors, E-Verify enrolled');

  const search = panel.getByRole('link', { name: 'Open LinkedIn search' });
  await expect(search).toHaveAttribute('href', /^https:\/\/www\.linkedin\.com\/search\/results\/people\/\?currentCompany=/);
  await expect(search).toHaveAttribute('target', '_blank');
  await expect(search).toHaveAttribute('rel', /noopener/);
  await expect(panel.getByRole('link', { name: 'Careers page' })).toHaveAttribute('target', '_blank');

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
  await expect(page.getByText('8 companies, 3 reached')).toBeVisible();
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
