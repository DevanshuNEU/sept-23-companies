import { expect, test } from './fixtures';

test('the page lists only replies; marking Luis Romero as replied lists him, and z takes it back', async ({ page, consoleErrors }) => {
  await page.goto('/linkedin');
  await expect(page.getByRole('heading', { level: 1, name: 'LinkedIn replies' })).toBeVisible();
  await expect(page.getByText(/^Last 7 days:/)).toHaveText('Last 7 days: 8 people messaged, 2 replied. In total: 11 people messaged, 2 replied.');
  await expect(page.getByRole('row').filter({ hasText: 'Mira Shah' })).toBeVisible();
  await expect(page.getByRole('row').filter({ hasText: 'Hannah Cole' })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'LinkedIn, 2 replies' })).toBeVisible();

  const luis = page.getByRole('row').filter({ hasText: 'Luis Romero' });
  await page.getByRole('textbox', { name: 'Mark someone as replied' }).fill('mintlify');
  await page.getByRole('button', { name: /^Luis Romero at Mintlify/ }).click();
  await page.getByRole('dialog', { name: 'What kind of reply?' }).getByRole('button', { name: 'Not now' }).click();
  await expect(page.getByText('Logged: reply from Luis Romero (Not now).')).toBeVisible();
  await expect(luis).toContainText('Replied today');

  await page.getByRole('heading', { level: 1, name: 'LinkedIn replies' }).click();
  await page.keyboard.press('z');
  await expect(luis).toHaveCount(0);
  await expect(page.getByText(/^Last 7 days:/)).toContainText('2 replied.');
  expect(consoleErrors).toEqual([]);
});

test('All recorded shows everyone he messaged or found, off by default', async ({ page, consoleErrors }) => {
  await page.goto('/linkedin');
  const all = page.getByRole('button', { name: /^All recorded/ });
  await expect(all).toHaveAttribute('aria-pressed', 'false');
  await all.click();
  await expect(page).toHaveURL(/[?&]all=1(&|$)/);
  await expect(page.getByRole('row').filter({ hasText: 'Hannah Cole' })).toContainText('Not started');
  await page.reload();
  await expect(all).toHaveAttribute('aria-pressed', 'true');
  await page.keyboard.press('v');
  await expect(page.getByRole('row').filter({ hasText: 'Hannah Cole' })).toHaveCount(0);
  expect(consoleErrors).toEqual([]);
});

for (const [width, height] of [[1440, 900], [1100, 800]] as const) {
  test(`at ${width} px every column fits and the list pages by company`, async ({ page, consoleErrors }) => {
    await page.setViewportSize({ width, height });
    await page.goto('/linkedin?scenario=many_linkedin');
    const footer = page.getByText('Showing 1 to 20 of 52 people');
    await expect(footer).toBeVisible();

    // Nothing reaches past the table's right edge, and the page never scrolls sideways.
    const fit = await page.evaluate(() => {
      const table = document.querySelector('[data-slot=table]');
      const card = table?.parentElement;
      const scroller = card?.closest('.overflow-y-auto');
      if (!table || !card || !scroller) return null;
      const right = card.getBoundingClientRect().right;
      const past = Array.from(card.querySelectorAll('th, td, button, a')).filter((el) => el.getBoundingClientRect().right > right + 0.5);
      return { past: past.length, sideways: scroller.scrollWidth > scroller.clientWidth };
    });
    expect(fit).toEqual({ past: 0, sideways: false });
    for (const name of ['Person', 'Kind', 'Why LinkedIn', 'Progress', 'Next']) {
      await expect(page.getByRole('columnheader', { name, exact: true })).toBeInViewport();
    }
    await expect(page.getByRole('row').filter({ hasText: 'Hannah Cole' })).toBeInViewport();

    await page.getByRole('button', { name: /^Next/ }).click();
    await expect(page.getByText('Page 2 of 3')).toBeVisible();
    await expect(page).toHaveURL(/[?&]page=2(&|$)/);
    await page.keyboard.press(']');
    await expect(page.getByText('Page 3 of 3')).toBeVisible();
    await expect(page.getByText('Showing 41 to 52 of 52 people')).toBeVisible();
    await page.reload();
    await expect(page.getByText('Page 3 of 3')).toBeVisible();
    expect(consoleErrors).toEqual([]);
  });
}
