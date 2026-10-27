import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient } from '@tanstack/react-query';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture, type FixtureDb } from '@/api/fixture/data';
import { AppShell } from '@/shell/app-shell';
import { renderWithApp } from '@/test/render';
import BrowsePage from './index';

/*
 * Browse companies (#120, the /browse page), on the `queue` sample: 8 picks, 12 companies on the bench and 30 more below
 * them. The table never names a company on today's list, pages 50 at a time, and "Use instead of..." swaps a pick.
 */

const press = (key: string, init: KeyboardEventInit = {}) => fireEvent.keyDown(document.body, { key, ...init });
const leavePage = () =>
  act(() => {
    window.dispatchEvent(new Event('pagehide'));
  });
const table = () => screen.getByRole('table', { name: 'Companies' });
const rows = () => Array.from(table().querySelectorAll<HTMLElement>('tbody tr[data-browse]'));
const names = () => rows().map((tr) => tr.querySelector('a')?.textContent);
const search = () => screen.getByRole('searchbox', { name: 'Search companies by name' });

async function open(o: { db?: FixtureDb; shell?: boolean } = {}) {
  const db = o.db ?? buildFixture('queue');
  const client = new FixtureClient(db);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = o.shell ? (
    <>
      <AppShell />
      <BrowsePage />
    </>
  ) : (
    <BrowsePage />
  );
  const view = renderWithApp(ui, { route: '/browse', client, queryClient });
  await screen.findByRole('heading', { level: 1, name: 'Browse companies' });
  await waitFor(() => expect(rows().length).toBeGreaterThan(0));
  return { ...view, client, db, queryClient };
}

describe('the Browse companies page', () => {
  it('is a full table with a label on every column and what each row says', async () => {
    await open();
    const heads = within(table()).getAllByRole('columnheader').map((h) => h.textContent);
    expect(heads).toEqual(['Rank', 'Company', 'Fit', 'Why it ranked', 'Open role', 'Size and stage', 'Visa', 'Email', 'Research', 'Action']);
    expect(screen.getByText(/Showing 42 of 42 companies/)).toBeInTheDocument();
    const first = rows()[0] as HTMLElement;
    expect(first).toHaveTextContent('Retell AI');
    expect(first).toHaveTextContent('Fit 88');
    expect(first).toHaveTextContent('Software Engineer, New Grad');
    expect(first).toHaveTextContent('Sponsorship not stated, E-Verify enrolled');
    expect(within(first).getByRole('group', { name: 'Research' })).toHaveTextContent('CareersWebsiteLinkedIn page');
    expect(within(first).getByRole('button', { name: /Use instead of/ })).toBeInTheDocument();
  });

  it('puts the cursor in the search box on arrival', async () => {
    await open();
    expect(search()).toHaveFocus();
  });

  it('lists in stored order and never a pick; every company is on one page of 50', async () => {
    await open();
    expect(rows()).toHaveLength(42);
    expect(names()[0]).toBe('Retell AI'); // the best ranked company that is not on today's list
    for (const pick of ['Pylon', 'Cerebrium', 'Resend']) expect(names()).not.toContain(pick);
    expect(new Set(names()).size).toBe(names().length);
    expect(screen.queryByRole('button', { name: 'Show more' })).not.toBeInTheDocument();
  });

  it('pages 50 at a time and Show more loads the next page without repeats', async () => {
    const user = userEvent.setup();
    const db = buildFixture('queue');
    const first = db.bench.companies[0];
    if (!first) throw new Error('fixture: a bench company');
    for (let i = 0; i < 20; i++) {
      const extra = structuredClone(first);
      extra.company = { ...extra.company, id: 800_000 + i, name: `Extra Co ${i}` };
      db.bench.companies.push(extra);
    }
    await open({ db });
    expect(rows()).toHaveLength(50);
    expect(screen.getByText(/Showing 50 of 62 companies/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Show more' }));
    await waitFor(() => expect(rows()).toHaveLength(62));
    expect(new Set(names()).size).toBe(62);
    expect(screen.queryByRole('button', { name: 'Show more' })).not.toBeInTheDocument();
  });

  it('typing searches by name and a filter chip narrows the list', async () => {
    const user = userEvent.setup();
    await open();
    await user.type(search(), 'retell');
    await waitFor(() => expect(names()).toEqual(['Retell AI']));
    await user.clear(search());
    await waitFor(() => expect(rows()).toHaveLength(42));
    const chip = screen.getByRole('button', { name: 'Visa not a no' });
    await user.click(chip);
    expect(chip).toHaveAttribute('aria-pressed', 'true');
    await waitFor(() => expect(rows().length).toBeLessThan(42));
    await user.type(search(), 'zzzz');
    expect(await screen.findByText(/No company matches/)).toBeInTheDocument();
  });

  it('a company held back shows its reason and has no button', async () => {
    const user = userEvent.setup();
    await open();
    await user.type(search(), 'cinder');
    await waitFor(() => expect(names()).toEqual(['Cinder']));
    const row = rows()[0] as HTMLElement;
    expect(row).toHaveTextContent('You said Not now to Cinder on Nov 2.');
    expect(within(row).queryByRole('button', { name: /Use instead of/ })).not.toBeInTheDocument();
  });

  it('the arrows move through the rows, Enter opens Use instead of..., a digit swaps', async () => {
    const { client } = await open();
    const swapped = vi.spyOn(client, 'swapPick');
    expect(rows()[0]).toHaveAttribute('aria-current', 'true');
    press('ArrowDown');
    expect(rows()[1]).toHaveAttribute('aria-current', 'true');
    press('ArrowUp');
    expect(rows()[0]).toHaveAttribute('aria-current', 'true');
    press('Enter');
    const chooser = await screen.findByRole('group', { name: 'Use Retell AI instead of' });
    expect(chooser).toHaveTextContent('It is not a Not now, and it uses no room.');
    const choices = within(chooser).getAllByRole('button');
    expect(choices[0]).toHaveTextContent('Pylon');
    expect(choices.map((b) => b.textContent).join(' ')).not.toMatch(/Resend|Baseten/); // reached
    press('1');
    expect(await screen.findByText('Swapped in Retell AI for Pylon.')).toBeInTheDocument();
    expect(names()).not.toContain('Retell AI'); // it is on today's list now
    expect(swapped).not.toHaveBeenCalled(); // still inside the undo window
    await leavePage();
    await waitFor(() => expect(swapped).toHaveBeenCalledTimes(1));
    expect(swapped.mock.calls[0]?.[1]).toMatchObject({ idempotencyKey: expect.stringMatching(/^pick-swap:\d+:\d+$/) });
    await waitFor(() => expect(names()).toContain('Pylon')); // the pick is back in the list
  });

  it('from the search box: type a name, Enter, then a digit swaps (focus moves to the chooser)', async () => {
    const user = userEvent.setup();
    const { client } = await open();
    const swapped = vi.spyOn(client, 'swapPick');
    await user.type(search(), 'railway');
    await waitFor(() => expect(names()).toEqual(['Railway']));
    await user.keyboard('{Enter}');
    const chooser = await screen.findByRole('group', { name: 'Use Railway instead of' });
    await waitFor(() => expect(chooser).toHaveFocus());
    await user.keyboard('2');
    expect(await screen.findByText(/^Swapped in Railway for /)).toBeInTheDocument();
    await leavePage();
    await waitFor(() => expect(swapped).toHaveBeenCalledTimes(1));
  });

  it('Escape closes the chooser and leaves the page alone', async () => {
    await open();
    press('Enter');
    await screen.findByRole('group', { name: /instead of/ });
    press('Escape');
    await waitFor(() => expect(screen.queryByRole('group', { name: /instead of/ })).not.toBeInTheDocument());
    expect(table()).toBeInTheDocument();
  });

  it('Z takes a swap back and nothing is sent', async () => {
    const { client } = await open({ shell: true });
    const swapped = vi.spyOn(client, 'swapPick');
    press('Enter');
    await screen.findByRole('group', { name: /instead of/ });
    press('1');
    await screen.findByText('Swapped in Retell AI for Pylon.');
    press('z');
    await waitFor(() => expect(names()).toContain('Retell AI'));
    await leavePage();
    expect(swapped).not.toHaveBeenCalled();
  });

  it('a swap works when Add to today is off for lack of room', async () => {
    const db = buildFixture('queue');
    db.bench.room = 0;
    db.bench.roomText = "No room to add more this week: the 7-day limit is taken by today's picks and the requests already sent.";
    const { client } = await open({ db });
    const swapped = vi.spyOn(client, 'swapPick');
    press('Enter');
    await screen.findByRole('group', { name: /instead of/ });
    press('1');
    expect(await screen.findByText('Swapped in Retell AI for Pylon.')).toBeInTheDocument();
    await leavePage();
    await waitFor(() => expect(swapped).toHaveBeenCalledTimes(1));
  });

  it('a click chooses too, and a pick he reached is not offered', async () => {
    const user = userEvent.setup();
    await open();
    await user.click(within(rows()[2] as HTMLElement).getByRole('button', { name: /Use instead of/ }));
    const chooser = await screen.findByRole('group', { name: /instead of/ });
    expect(within(chooser).queryByRole('button', { name: /Resend|Baseten/ })).not.toBeInTheDocument();
    await user.click(within(chooser).getAllByRole('button')[1] as HTMLElement);
    expect(await screen.findByText(/^Swapped in .* for Cerebrium\.$/)).toBeInTheDocument();
  });
});
