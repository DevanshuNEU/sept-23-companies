import { expect, test } from './fixtures';

test('fixing Cerebrium domain takes it off the list and the sidebar badge drops to 4', async ({ page, consoleErrors }) => {
  await page.goto('/needs-you');
  await expect(page.getByRole('heading', { level: 1, name: 'Needs you' })).toBeVisible();
  const sidebarLink = page.getByRole('link', { name: /^Needs you/ });
  await expect(sidebarLink).toHaveAccessibleName('Needs you, 5 open');

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
  await expect(sidebarLink).toHaveAccessibleName('Needs you, 4 open');
  expect(consoleErrors).toEqual([]);
});
