import { expect, test } from './fixtures';

test('r then z leaves Hannah Cole at Not started', async ({ page, consoleErrors }) => {
  await page.goto('/linkedin');
  const hannah = page.getByRole('row').filter({ hasText: 'Hannah Cole' });
  await expect(hannah).toContainText('Not started');

  await hannah.click();
  // The click writes ?sel= through a router transition; a key pressed before it commits finds nothing selected.
  await expect(hannah).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('r');
  await expect(page.getByText('Logged: request sent to Hannah Cole.')).toBeVisible();
  await expect(hannah).toContainText('Requested today');

  await page.keyboard.press('z');
  await expect(hannah).toContainText('Not started');
  await expect(page.getByRole('tab', { name: /^To reach/ })).toContainText('12');
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
    await expect(page.getByRole('row').filter({ hasText: 'Hannah Cole' }).getByRole('button', { name: /Log request/ })).toBeInViewport();

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
