import type { Page } from '@playwright/test';
import { expect, test } from './fixtures';

test('fixing Cerebrium domain takes it off the list and the sidebar badge drops to 4', async ({ page, consoleErrors }) => {
  await page.goto('/needs-you');
  await expect(page.getByRole('heading', { level: 1, name: 'Needs you' })).toBeVisible();
  const sidebarLink = page.getByRole('navigation', { name: 'Views' }).getByRole('link', { name: /^Needs you, / });
  await expect(sidebarLink).toHaveAccessibleName(/^Needs you, 5 open(,|$)/);

  const cerebrium = page.getByRole('article', { name: 'Cerebrium' });
  await cerebrium.getByRole('button', { name: /^Set domain/ }).click();
  await cerebrium.getByRole('textbox', { name: 'Email domain' }).fill('cerebrium.example');
  const save = cerebrium.getByRole('button', { name: 'Save and lock' });
  await expect(save).toHaveAttribute('aria-disabled', 'true');
  await cerebrium.getByRole('textbox', { name: 'Source' }).fill('Company website footer');
  await expect(save).toHaveAttribute('aria-disabled', 'false');
  await save.click();

  await expect(page.getByText("Saved and locked. Cerebrium's drafts will be checked now.")).toBeVisible();
  await expect(page.getByRole('article', { name: 'Cerebrium' })).toHaveCount(0);
  await expect(sidebarLink).toHaveAccessibleName(/^Needs you, 4 open(,|$)/);
  expect(consoleErrors).toEqual([]);
});

// The review scenario (#16): every kind, the Mesh Systems and Mesh merge question, and two items settled today.
const REVIEW = '/needs-you?scenario=review';
const MESH = 'Mesh Systems and Mesh';

const sidebarCount = (page: Page) => page.getByRole('navigation', { name: 'Views' }).getByRole('link', { name: /^Needs you, / });

/** Leaving the view commits what waits in the 8 second undo window; coming back shows what the client stored. */
async function commitByLeaving(page: Page) {
  await page.keyboard.press('g');
  await page.keyboard.press('t');
  await expect(page).toHaveURL(/\/today/);
  await page.keyboard.press('g');
  await page.keyboard.press('n');
  await expect(page.getByRole('heading', { level: 1, name: 'Needs you' })).toBeVisible();
}

test('each of the five triggers is settled through the page with its own fix', async ({ page, consoleErrors }) => {
  await page.goto('/needs-you');
  await expect(page.getByTestId('ny-progress')).toHaveText('5 open');

  // 5, a missing domain: set it with a source.
  const cerebrium = page.getByRole('article', { name: 'Cerebrium' });
  await cerebrium.getByRole('button', { name: 'Set domain' }).click();
  await cerebrium.getByRole('textbox', { name: 'Email domain' }).fill('cerebrium.example');
  await cerebrium.getByRole('textbox', { name: 'Source' }).fill('Company website footer');
  await cerebrium.getByRole('button', { name: 'Save and lock' }).click();
  await expect(cerebrium).toHaveCount(0);

  // 4, an unknown size: a number with a source, saved with mod+enter.
  const trigger = page.getByRole('article', { name: 'Trigger.dev' });
  await trigger.click();
  await page.keyboard.press('e');
  await trigger.getByRole('textbox', { name: 'Size' }).fill('34');
  await trigger.getByRole('textbox', { name: 'Source' }).fill('LinkedIn company page, 34 employees');
  await page.keyboard.press('ControlOrMeta+Enter');
  await expect(trigger).toHaveCount(0);

  // 3, unclear sponsorship: no-go with a reason and a source.
  await page.getByRole('article', { name: 'Hex' }).click();
  await page.keyboard.press('n');
  const dialog = page.getByRole('alertdialog', { name: 'Add Hex to the no-go list?' });
  await dialog.getByRole('textbox', { name: 'Source' }).fill('Hex careers FAQ');
  await dialog.getByRole('button', { name: 'Mark no-go' }).click();
  await expect(page.getByText('Hex is on the no-go list.')).toBeVisible();

  // 1, an unreadable board: mark ok.
  await page.getByRole('article', { name: 'Zed Industries' }).getByRole('button', { name: 'Mark ok' }).click();
  await expect(page.getByText('Marked ok. Zed Industries keeps its postings open.')).toBeVisible();

  // 2, a board with zero jobs: give the new board.
  const planetscale = page.getByRole('article', { name: 'PlanetScale' });
  await planetscale.getByRole('button', { name: 'Edit board' }).click();
  await planetscale.getByRole('textbox', { name: 'Board URL' }).fill('https://jobs.ashbyhq.com/planetscale');
  await planetscale.getByRole('textbox', { name: 'Source' }).fill('Their careers page links here now');
  await planetscale.getByRole('button', { name: 'Save and lock' }).click();

  const settled = page.getByRole('heading', { level: 2, name: 'All settled.' });
  await expect(settled).toBeFocused();
  await expect(page.getByText('You settled 5 today.')).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Views' }).getByRole('link', { name: 'Needs you', exact: true })).toBeVisible();
  expect(consoleErrors).toEqual([]);
});

test('merge two companies with one key, then undo it from Resolved', async ({ page, consoleErrors }) => {
  await page.goto(REVIEW);
  const mesh = page.getByRole('article', { name: MESH });
  await mesh.click();
  await expect(mesh.getByRole('table', { name: `${MESH}, side by side` })).toBeVisible();
  await page.keyboard.press('m');
  await expect(page.getByText('Merged Mesh into Mesh Systems.')).toBeVisible();
  await expect(page.getByRole('article', { name: MESH })).toHaveCount(0);

  await commitByLeaving(page);
  await expect(page.getByRole('article', { name: MESH })).toHaveCount(0);
  await page.getByRole('button', { name: 'Show' }).click();
  const row = page.getByRole('row', { name: /^Mesh Systems/ });
  await expect(row).toContainText('Merged Mesh into Mesh Systems by you');
  await row.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByText("Undone. The merge is taken back, and Mesh Systems' duplicate question is open again.")).toBeVisible();
  await expect(page.getByRole('article', { name: MESH })).toBeVisible();
  await expect(sidebarCount(page)).toHaveAccessibleName(/^Needs you, 11 open(,|$)/);
  expect(consoleErrors).toEqual([]);
});

test('settle every item by keys and land on All settled with the focus on its heading', async ({ page, consoleErrors }) => {
  await page.goto(REVIEW);
  await expect(page.getByTestId('ny-progress')).toHaveText('2 settled today, 11 open');
  // The first item is selected on arrival; each o settles the selected one and moves to the next.
  for (let open = 11; open > 0; open -= 1) {
    await expect(sidebarCount(page)).toHaveAccessibleName(new RegExp(`^Needs you, ${open} open(,|$)`));
    await page.keyboard.press('o');
  }
  const settled = page.getByRole('heading', { level: 2, name: 'All settled.' });
  await expect(settled).toBeFocused();
  await expect(page.getByText('You settled 13 today.')).toBeVisible();
  await expect(page.getByText(/^Boards are read again Thu Nov 12 at 08:00\./)).toBeVisible();
  await expect(page.getByRole('link', { name: /^Open the Outbox/ })).toBeVisible();
  await expect(page.getByTestId('ny-progress')).toHaveCount(0);
  await expect(page.getByRole('status').filter({ hasText: 'All settled.' })).toHaveCount(1);
  expect(consoleErrors).toEqual([]);
});

test('an edited size leaves the list and its Resolved row has no Undo', async ({ page, consoleErrors }) => {
  await page.goto(REVIEW);
  const fernhill = page.getByRole('article', { name: 'Fernhill' });
  await fernhill.click();
  await page.keyboard.press('e');
  await fernhill.getByRole('textbox', { name: 'Size' }).fill('about 60');
  await fernhill.getByRole('textbox', { name: 'Source' }).fill('LinkedIn company page');
  await page.keyboard.press('ControlOrMeta+Enter');
  await expect(page.getByText("Saved and locked. Fernhill's size is about 60.")).toBeVisible();
  await expect(page.getByRole('article', { name: 'Fernhill' })).toHaveCount(0);

  await commitByLeaving(page);
  await page.getByRole('button', { name: 'Show' }).click();
  const row = page.getByRole('row', { name: /^Fernhill/ });
  await expect(row).toContainText('Size set to about 60 by you, from LinkedIn company page. To change it, set it again on the company page.');
  await expect(row).toContainText('Edited, locked');
  await expect(row.getByRole('button')).toHaveCount(0);
  expect(consoleErrors).toEqual([]);
});

test('the sidebar count drops with each fix and rises on Undo', async ({ page, consoleErrors }) => {
  await page.goto(REVIEW);
  await expect(sidebarCount(page)).toHaveAccessibleName(/^Needs you, 11 open(,|$)/);
  await page.getByRole('article', { name: 'Quillmate' }).getByRole('button', { name: 'Mark ok' }).click();
  await expect(sidebarCount(page)).toHaveAccessibleName(/^Needs you, 10 open(,|$)/);
  await page.getByRole('article', { name: 'Brightwell' }).getByRole('button', { name: 'Mark ok' }).click();
  await expect(sidebarCount(page)).toHaveAccessibleName(/^Needs you, 9 open(,|$)/);
  // Inside the window, Z undoes the last fix: nothing is sent and the item is back at once.
  await page.keyboard.press('z');
  await expect(page.getByRole('article', { name: 'Brightwell' })).toBeVisible();
  await expect(sidebarCount(page)).toHaveAccessibleName(/^Needs you, 10 open(,|$)/);

  // After the window, Undo from Resolved asks the server, and the count rises again.
  await commitByLeaving(page);
  await page.getByRole('button', { name: 'Show' }).click();
  await page.getByRole('row', { name: /^Ambergate/ }).getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByText("Undone. Ambergate's sponsorship question is open again.")).toBeVisible();
  await expect(sidebarCount(page)).toHaveAccessibleName(/^Needs you, 11 open(,|$)/);
  await expect(page.getByRole('article', { name: 'Ambergate' })).toBeVisible();
  expect(consoleErrors).toEqual([]);
});

test('no console error and no red element on the page', async ({ page, consoleErrors }) => {
  await page.goto(REVIEW);
  await expect(page.getByRole('article', { name: MESH })).toBeVisible();
  await page.getByRole('button', { name: 'Show' }).click();
  await expect(page.getByRole('row', { name: /^Ambergate/ })).toBeVisible();
  const red = await page.evaluate(() => {
    // The red tokens as the browser resolves them, then every visible element's colors against them.
    const probe = document.createElement('i');
    document.body.append(probe);
    const tokens = ['--bad', '--bad-bg', '--bad-bd', '--bad-dot', '--destructive'].map((v) => {
      probe.style.color = `var(${v})`;
      return getComputedStyle(probe).color;
    });
    probe.remove();
    const reds = new Set(tokens.filter((c) => c && c !== 'rgba(0, 0, 0, 0)'));
    const hits: string[] = [];
    for (const el of Array.from(document.querySelectorAll<HTMLElement>('main *, [data-slot="sidebar"] *'))) {
      if (el.getClientRects().length === 0) continue;
      const cs = getComputedStyle(el);
      for (const prop of ['color', 'backgroundColor', 'borderTopColor', 'borderLeftColor', 'fill', 'stroke'] as const) {
        const value = cs[prop];
        if (reds.has(value) && !(prop.startsWith('border') && cs.borderTopWidth === '0px' && cs.borderLeftWidth === '0px')) {
          hits.push(`${el.tagName.toLowerCase()}.${el.className} ${prop} ${value}`);
        }
      }
    }
    return { count: reds.size, hits };
  });
  expect(red.count).toBeGreaterThan(0);
  expect(red.hits).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
