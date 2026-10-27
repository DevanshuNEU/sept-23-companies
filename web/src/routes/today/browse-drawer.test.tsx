import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient } from '@tanstack/react-query';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture, type FixtureDb } from '@/api/fixture/data';
import { renderWithApp } from '@/test/render';
import { AppShell } from '@/shell/app-shell';
import TodayPage from './index';

/*
 * Browse companies (#120, F), on the `queue` sample: 8 picks, 12 companies on the bench and 30 more below them. The list
 * never names a company on today's list, pages 25 at a time, and "Use instead of..." swaps a pick for the company.
 */

const press = (key: string, init: KeyboardEventInit = {}) => fireEvent.keyDown(document.body, { key, ...init });
const leavePage = () =>
  act(() => {
    window.dispatchEvent(new Event('pagehide'));
  });
const rail = () => within(screen.getByRole('listbox', { name: "Today's picks" })).getAllByRole('option');
const drawer = () => screen.getByRole('dialog', { name: /Browse companies/ });
const rows = () => within(within(drawer()).getByRole('list', { name: 'Companies' })).getAllByRole('listitem');
const names = () => rows().map((li) => li.querySelector('a')?.textContent);

async function open(o: { db?: FixtureDb; shell?: boolean } = {}) {
  const db = o.db ?? buildFixture('queue');
  const client = new FixtureClient(db);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = o.shell ? (
    <>
      <AppShell />
      <TodayPage />
    </>
  ) : (
    <TodayPage />
  );
  const view = renderWithApp(ui, { route: '/today', client, queryClient });
  await screen.findByRole('heading', { level: 2, name: 'Pylon' });
  return { ...view, client, db, queryClient };
}

async function browse() {
  press('f');
  await screen.findByRole('dialog', { name: /Browse companies/ });
  await waitFor(() => expect(rows().length).toBeGreaterThan(0));
}

describe('the Browse companies drawer', () => {
  it('F opens it and Escape closes it', async () => {
    await open();
    await browse();
    expect(drawer()).toHaveTextContent(/\d+ companies, best ranked first\. A swap uses no room this week\./);
    fireEvent.keyDown(document.body, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /Browse companies/ })).not.toBeInTheDocument());
  });

  it('lists a page of 25 in stored order, never a pick, and Show more loads the next page', async () => {
    const user = userEvent.setup();
    await open();
    await browse();
    expect(rows()).toHaveLength(25);
    expect(names()[0]).toBe('Retell AI'); // the best ranked company that is not on today's list
    for (const pick of ['Pylon', 'Cerebrium', 'Resend']) expect(names()).not.toContain(pick);
    await user.click(within(drawer()).getByRole('button', { name: 'Show more' }));
    await waitFor(() => expect(rows().length).toBeGreaterThan(25));
    expect(new Set(names()).size).toBe(names().length);
  });

  it('typing searches by name and a filter chip narrows the list', async () => {
    const user = userEvent.setup();
    await open();
    await browse();
    await user.type(within(drawer()).getByRole('searchbox', { name: 'Search companies by name' }), 'retell');
    await waitFor(() => expect(names()).toEqual(['Retell AI']));
    await user.clear(within(drawer()).getByRole('searchbox'));
    await waitFor(() => expect(rows()).toHaveLength(25));
    const before = within(drawer()).getByText(/\d+ companies,/).textContent;
    await user.click(within(drawer()).getByRole('button', { name: 'Visa not a no' }));
    expect(within(drawer()).getByRole('button', { name: 'Visa not a no' })).toHaveAttribute('aria-pressed', 'true');
    await waitFor(() => expect(within(drawer()).getByText(/\d+ companies,/).textContent).not.toBe(before));
    await user.type(within(drawer()).getByRole('searchbox'), 'zzzz');
    expect(await within(drawer()).findByText(/No company matches/)).toBeInTheDocument();
  });

  it('a company held back shows its reason and has no button', async () => {
    const user = userEvent.setup();
    await open();
    await browse();
    await user.type(within(drawer()).getByRole('searchbox'), 'cinder');
    await waitFor(() => expect(names()).toEqual(['Cinder']));
    expect(rows()[0]).toHaveTextContent('You said Not now to Cinder on Nov 2.');
    expect(within(rows()[0] as HTMLElement).queryByRole('button', { name: /Use instead of/ })).not.toBeInTheDocument();
  });

  it('the arrows move through the rows, Enter opens Use instead of..., a digit swaps and the drawer closes', async () => {
    const { client } = await open();
    const swapped = vi.spyOn(client, 'swapPick');
    await browse();
    expect(rows()[0]).toHaveAttribute('aria-current', 'true');
    press('ArrowDown');
    expect(rows()[1]).toHaveAttribute('aria-current', 'true');
    press('ArrowUp');
    expect(rows()[0]).toHaveAttribute('aria-current', 'true');
    press('Enter');
    const chooser = await within(drawer()).findByRole('group', { name: 'Use Retell AI instead of' });
    expect(chooser).toHaveTextContent('It is not a Not now, and it uses no room.');
    const choices = within(chooser).getAllByRole('button');
    expect(choices[0]).toHaveTextContent('Pylon');
    expect(choices.map((b) => b.textContent)).not.toEqual(expect.arrayContaining([expect.stringContaining('Resend')])); // reached
    press('1');
    expect(await screen.findByText('Swapped in Retell AI for Pylon.')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /Browse companies/ })).not.toBeInTheDocument());
    const names8 = rail().map((o) => o.querySelector('b')?.textContent);
    expect(names8).toContain('Retell AI');
    expect(names8).not.toContain('Pylon');
    expect(rail()).toHaveLength(8);
    expect(swapped).not.toHaveBeenCalled(); // still inside the undo window
    await leavePage();
    await waitFor(() => expect(swapped).toHaveBeenCalledTimes(1));
    expect(swapped.mock.calls[0]?.[1]).toMatchObject({ idempotencyKey: expect.stringMatching(/^pick-swap:\d+:\d+$/) });
  });

  it('Escape closes the chooser first and the drawer second', async () => {
    await open();
    await browse();
    press('Enter');
    await within(drawer()).findByRole('group', { name: /instead of/ });
    fireEvent.keyDown(document.body, { key: 'Escape' });
    await waitFor(() => expect(within(drawer()).queryByRole('group', { name: /instead of/ })).not.toBeInTheDocument());
    expect(drawer()).toBeInTheDocument();
    fireEvent.keyDown(document.body, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /Browse companies/ })).not.toBeInTheDocument());
  });

  it('Z takes a swap back and nothing is sent', async () => {
    const { client } = await open({ shell: true });
    const swapped = vi.spyOn(client, 'swapPick');
    await browse();
    press('Enter');
    await within(drawer()).findByRole('group', { name: /instead of/ });
    press('1');
    await screen.findByText('Swapped in Retell AI for Pylon.');
    press('z');
    await waitFor(() => expect(rail().map((o) => o.querySelector('b')?.textContent)).toContain('Pylon'));
    await leavePage();
    expect(swapped).not.toHaveBeenCalled();
  });

  it('a swap works when Add to today is off for lack of room', async () => {
    const db = buildFixture('queue');
    db.bench.room = 0;
    db.bench.roomText = "No room to add more this week: the 7-day limit is taken by today's picks and the requests already sent.";
    const { client } = await open({ db });
    const swapped = vi.spyOn(client, 'swapPick');
    await browse();
    press('Enter');
    await within(drawer()).findByRole('group', { name: /instead of/ });
    press('1');
    expect(await screen.findByText('Swapped in Retell AI for Pylon.')).toBeInTheDocument();
    await leavePage();
    await waitFor(() => expect(swapped).toHaveBeenCalledTimes(1));
  });

  it('a click chooses too, and a pick he reached is not offered', async () => {
    const user = userEvent.setup();
    await open();
    await browse();
    await user.click(within(rows()[2] as HTMLElement).getByRole('button', { name: /Use instead of/ }));
    const chooser = await within(drawer()).findByRole('group', { name: /instead of/ });
    expect(within(chooser).queryByRole('button', { name: /Resend|Baseten/ })).not.toBeInTheDocument();
    await user.click(within(chooser).getAllByRole('button')[1] as HTMLElement);
    expect(await screen.findByText(/^Swapped in .* for Cerebrium\.$/)).toBeInTheDocument();
  });
});
