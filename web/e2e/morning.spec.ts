import type { Page } from '@playwright/test';
import { expect, test } from './fixtures';

// The app's own host: 5173 by default, or the workspace port when WEB_PORT is set (see playwright.config.ts).
const HOST = `127.0.0.1:${process.env.WEB_PORT ?? '5173'}`;

/** Records every request host so the run can prove the app never left the dev server. */
function watchRequests(page: Page): string[] {
  const foreign: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.protocol === 'data:' || url.protocol === 'blob:' || url.protocol === 'about:') return;
    if (url.host !== HOST) foreign.push(request.url());
  });
  return foreign;
}

/**
 * On every page: nothing went to another host, there is no console error, and no button is named "Send...".
 * Only the Outbox's "Sending 3" group toggle (a status heading) is allowed; "Sends ..." or "Sender ..." would be caught.
 */
async function expectCleanPage(page: Page, consoleErrors: string[], foreign: string[]) {
  await expect(page.getByRole('button', { name: /^Send(?!ing \d+$)/i })).toHaveCount(0);
  expect(consoleErrors).toEqual([]);
  expect(foreign).toEqual([]);
}

// The phase-1 Today, now at /proposals: its own capacity box. The sidebar count reads the plan (2 of 7) and does not move on a pick.
const picked = (page: Page) => page.getByLabel('Capacity').locator('div').filter({ hasText: /^Picked today/ });

const outboxLink = (page: Page) => page.getByRole('navigation', { name: 'Views' }).getByRole('link', { name: /^Outbox/ });

test('the morning flow: reply, pick, fix, approve, LinkedIn, Done', async ({ page, consoleErrors }) => {
  const foreign = watchRequests(page);
  // The clock must run before the first load so runFor can drive the 8 second undo window.
  await page.clock.install({ time: new Date('2026-11-10T15:14:00Z') });

  // 1. Land on Today: the plan of people, on the first person to do.
  await page.goto('/today');
  await expect(page.getByRole('heading', { level: 1, name: 'Today' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Tuesday, November 10' })).toBeVisible();
  await expect(page.locator('[data-slot="sidebar"]').getByRole('link', { name: /^Today/ })).toContainText('2 of 7');
  await expect(page.getByText('7 people, 2 done')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Selected person' }).getByRole('heading', { level: 2, name: 'Lena Ortiz' })).toBeVisible();
  await expectCleanPage(page, consoleErrors, foreign);

  // 2. Outbox: open the reply and tag it Interested.
  await page.keyboard.press('g');
  await page.keyboard.press('o');
  await expect(page).toHaveURL(/\/outbox/);
  await expect(page.getByRole('heading', { level: 1, name: 'Outbox' })).toBeVisible();
  await expect(outboxLink(page)).toHaveAccessibleName('Outbox, 1 reply to handle, 7 drafts to review');
  const priya = page.getByRole('option').filter({ hasText: 'Priya Natarajan' });
  await priya.click();
  await expect(priya).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByText('Browserbase is on hold')).toBeVisible();
  const interested = page.getByRole('group', { name: 'Reply kind' }).getByRole('button', { name: 'Interested' });
  await interested.click();
  await expect(interested).toHaveAttribute('aria-pressed', 'true');
  await expectCleanPage(page, consoleErrors, foreign);

  // 3. Today, then All proposed companies: pick Pylon with x. Nothing is selected there on load, so the first j
  // lands on Pylon.
  await page.keyboard.press('g');
  await page.keyboard.press('t');
  await expect(page.getByText('7 people, 2 done')).toBeVisible();
  await page.getByRole('link', { name: 'All proposed companies' }).click();
  await expect(page).toHaveURL(/\/proposals$/);
  await expect(picked(page)).toContainText('3 of 5');
  await page.keyboard.press('j');
  await expect(page.getByRole('complementary', { name: 'Pylon preview' })).toBeVisible();
  await page.keyboard.press('x');
  await expect(picked(page)).toContainText('4 of 5');
  await expect(page.getByRole('checkbox', { name: 'Pick Pylon' })).toBeChecked();
  await expectCleanPage(page, consoleErrors, foreign);

  // 4. Needs you: fix Cerebrium's domain.
  await page.keyboard.press('g');
  await page.keyboard.press('n');
  await expect(page.getByRole('heading', { level: 1, name: 'Needs you' })).toBeVisible();
  const needsYou = page.getByRole('navigation', { name: 'Views' }).getByRole('link', { name: /^Needs you, / });
  await expect(needsYou).toHaveAccessibleName(/^Needs you, 5 open(,|$)/);
  const cerebrium = page.getByRole('article', { name: 'Cerebrium' });
  await cerebrium.getByRole('button', { name: /^Set domain/ }).click();
  await cerebrium.getByRole('textbox', { name: 'Email domain' }).fill('cerebrium.example');
  await cerebrium.getByRole('textbox', { name: 'Source' }).fill('Company website footer');
  const save = cerebrium.getByRole('button', { name: 'Save and lock' });
  await expect(save).toHaveAttribute('aria-disabled', 'false');
  await save.click();
  await expect(page.getByText("Saved and locked. Cerebrium's drafts will be checked now.")).toBeVisible();
  await expect(page.getByRole('article', { name: 'Cerebrium' })).toHaveCount(0);
  await expect(needsYou).toHaveAccessibleName(/^Needs you, 4 open(,|$)/);
  await expectCleanPage(page, consoleErrors, foreign);

  // 5. Outbox: approve Omar with a, wait out the undo window, see him under Approved and drafts at 6.
  await page.keyboard.press('g');
  await page.keyboard.press('o');
  await expect(page.getByRole('heading', { level: 1, name: 'Outbox' })).toBeVisible();
  const omar = page.getByRole('option').filter({ hasText: 'Omar Haddad' });
  await omar.click();
  await expect(omar).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('heading', { level: 2, name: 'Omar Haddad' })).toBeVisible();
  await page.keyboard.press('a');
  await expect(page.getByText('Approved Omar Haddad. Sends after 10:55 ET.')).toBeVisible();
  await page.clock.runFor(8000);
  await expect(page.getByRole('button', { name: 'Undo' })).toHaveCount(0);
  await expect(outboxLink(page)).toHaveAccessibleName('Outbox, 1 reply to handle, 6 drafts to review');
  // Approving moved the selection on to the next draft, which let Omar settle into Approved.
  await expect(omar).toHaveAttribute('aria-selected', 'false');
  const approved = page.getByRole('listbox', { name: 'Approved' });
  await expect(approved.getByRole('option').filter({ hasText: 'Omar Haddad' })).toBeVisible();
  await expectCleanPage(page, consoleErrors, foreign);

  // 6. LinkedIn: log a request to Hannah Cole.
  await page.keyboard.press('g');
  await page.keyboard.press('l');
  await expect(page).toHaveURL(/\/linkedin/);
  const hannah = page.getByRole('row').filter({ hasText: 'Hannah Cole' });
  await expect(hannah).toContainText('Not started');
  await hannah.click();
  await expect(hannah).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('r');
  await expect(page.getByText('Logged: request sent to Hannah Cole.')).toBeVisible();
  await expect(hannah).toContainText('Requested today');
  await expectCleanPage(page, consoleErrors, foreign);

  // 7. Done.
  await page.keyboard.press('g');
  await page.keyboard.press('d');
  await expect(page).toHaveURL(/\/done$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Done companies' })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'Crusoe', exact: true })).toBeVisible();
  await expectCleanPage(page, consoleErrors, foreign);
});

test('every view loads clean: no foreign host, no console error, no Send button', async ({ page, consoleErrors }) => {
  const foreign = watchRequests(page);
  const views: [string, string][] = [
    ['/today', 'Today'],
    ['/proposals', 'All proposed companies'],
    ['/outbox', 'Outbox'],
    ['/linkedin', 'LinkedIn list'],
    ['/needs-you', 'Needs you'],
    ['/no-go', 'No-go list'],
    ['/done', 'Done companies'],
  ];
  for (const [path, heading] of views) {
    await page.goto(path);
    await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
    await expectCleanPage(page, consoleErrors, foreign);
  }
});

test('a blocked draft shows its block in full on a 1280 by 800 screen (the detail scrolls, nothing is squeezed)', async ({ page, consoleErrors }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/outbox/2002');
  await expect(page.getByRole('heading', { level: 2, name: 'Kenji Watanabe' })).toBeVisible();
  await expect(page.getByText('Blocking:')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Compare drafts' })).toBeInViewport({ ratio: 1 });
  expect(consoleErrors).toEqual([]);
});

const geometry = (box: { x: number; y: number; width: number; height: number } | null) => {
  if (!box) throw new Error('No box');
  return { l: box.x, t: box.y, r: box.x + box.width, b: box.y + box.height };
};
const intersect = (a: ReturnType<typeof geometry>, b: ReturnType<typeof geometry>) => a.l < b.r && b.l < a.r && a.t < b.b && b.t < a.b;

for (const width of [1440, 1280]) {
  test(`Proposals at ${width}: the selected row's actions never cover its Sponsorship badge`, async ({ page, consoleErrors }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/proposals');
    await expect(page.getByRole('heading', { name: 'Tuesday, November 10' })).toBeVisible();
    await page.keyboard.press('j');
    await expect(page.getByRole('complementary', { name: 'Pylon preview' })).toBeVisible();
    const row = page.getByRole('row').filter({ hasText: 'Pylon' }).filter({ hasText: 'Says it sponsors' });
    await row.hover();
    const remove = row.getByRole('button', { name: 'Remove Pylon' });
    const badge = row.getByText('Says it sponsors');
    // The actions show on hover at both sizes, and on the selected row from 1400 px.
    await expect(remove.locator('xpath=..')).toHaveCSS('opacity', '1');
    if (await badge.isVisible()) {
      expect(intersect(geometry(await badge.boundingBox()), geometry(await remove.boundingBox()))).toBe(false);
      expect(intersect(geometry(await badge.boundingBox()), geometry(await row.getByRole('button', { name: 'Move Pylon to the backlog' }).boundingBox()))).toBe(false);
    } else {
      // Under 1400 px the badge steps aside while the actions show; it is never covered.
      expect(width).toBeLessThan(1400);
    }
    await page.mouse.move(0, 0);
    await expect(badge).toBeVisible();
    expect(consoleErrors).toEqual([]);
  });
}

test('1280: no page or table scrolls sideways on Today, Proposals, Done and No-go', async ({ page, consoleErrors }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  for (const [path, heading, ready] of [
    ['/today', 'Today', 'listbox'],
    ['/proposals', 'All proposed companies', 'table'],
    ['/done', 'Done companies', 'table'],
    ['/no-go', 'No-go list', 'table'],
  ] as const) {
    await page.goto(path);
    await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
    await expect(page.getByRole(ready).first()).toBeVisible();
    const sideways = await page.evaluate(() => {
      const bad: string[] = [];
      if (document.documentElement.scrollWidth > document.documentElement.clientWidth) bad.push('page');
      for (const el of Array.from(document.querySelectorAll('*'))) {
        const x = getComputedStyle(el).overflowX;
        if ((x === 'auto' || x === 'scroll') && el.scrollWidth > el.clientWidth) bad.push(`${el.tagName}.${String(el.className).slice(0, 40)}`);
      }
      return bad;
    });
    expect(sideways, path).toEqual([]);
  }
  expect(consoleErrors).toEqual([]);
});

test('1440: No-go shows its Added column and row menu in the viewport', async ({ page, consoleErrors }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/no-go');
  await expect(page.getByRole('heading', { level: 1, name: 'No-go list' })).toBeVisible();
  const table = page.getByRole('table', { name: /./ }).first();
  await expect(table.getByRole('columnheader', { name: 'Added' })).toBeInViewport({ ratio: 1 });
  await expect(table.getByRole('button', { name: 'More' }).first()).toBeInViewport({ ratio: 1 });
  expect(consoleErrors).toEqual([]);
});

test('Needs you starts at the left edge like the other views', async ({ page, consoleErrors }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const left = async (path: string, heading: string) => {
    await page.goto(path);
    const h1 = page.getByRole('heading', { level: 1, name: heading });
    await expect(h1).toBeVisible();
    return (await h1.boundingBox())?.x ?? -1;
  };
  const done = await left('/done', 'Done companies');
  const needsYou = await left('/needs-you', 'Needs you');
  expect(needsYou).toBeGreaterThan(0);
  expect(needsYou).toBe(done);
  expect(consoleErrors).toEqual([]);
});
