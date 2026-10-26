import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { queryKeys, type PlanRow } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture, type FixtureDb } from '@/api/fixture/data';
import { AppShell } from '@/shell/app-shell';
import { renderWithApp } from '@/test/render';
import TodayPage from './index';

/*
 * Today as a company queue (#9 company-first ruling, P2), on the `queue` sample: 8 companies, Resend and Baseten
 * reached, Pylon first to do (with his saved search), Turso carried from Monday, 5 companies in flight.
 */

const press = (key: string, init: KeyboardEventInit = {}) => fireEvent.keyDown(document.body, { key, ...init });
const options = () => within(screen.getByRole('listbox', { name: "Today's companies" })).getAllByRole('option');
const option = (n: number) => {
  const o = options()[n - 1];
  if (!o) throw new Error(`No option ${n}`);
  return o;
};
const panel = () => screen.getByRole('region', { name: 'Selected company' });
const leavePage = () =>
  act(() => {
    window.dispatchEvent(new Event('pagehide'));
  });

function stubClipboard() {
  const writeText = vi.fn<(text: string) => Promise<void>>(async () => {});
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
  return writeText;
}

async function open(o: { db?: FixtureDb; shell?: boolean; queryClient?: QueryClient } = {}) {
  const db = o.db ?? buildFixture('queue');
  const client = new FixtureClient(db);
  const queryClient = o.queryClient ?? new QueryClient({ defaultOptions: { queries: { retry: false } } });
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

const rowOf = (db: FixtureDb, name: string): PlanRow => {
  const row = db.plan.rows.find((r) => r.company.name === name);
  if (!row) throw new Error(`No row for ${name}`);
  return row;
};

afterEach(() => {
  toast.dismiss();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe('Today as a company queue', () => {
  it('lands on the first company to do and shows every line the API gives', async () => {
    const { db } = await open();
    const pylon = rowOf(db, 'Pylon');
    expect(screen.getByText('8 companies, 2 reached')).toBeInTheDocument();
    expect(option(3)).toHaveAttribute('aria-selected', 'true');
    expect(option(3)).toHaveAccessibleName('3. Pylon, To do');
    expect(option(1)).toHaveAccessibleName('1. Resend, Done');
    expect(option(1)).toHaveTextContent('3 asked today, room for 1 more, free again Nov 24');
    const about = within(panel()).getByLabelText('About Pylon');
    for (const [label, text] of [
      ['Size and stage', '30 people (estimate), Series A'],
      ['Role', 'Software Engineer I, posted 1 day ago'],
      ['Hiring now', '3 new engineering roles in the last 30 days'],
      ['Sponsorship', 'Says it sponsors, E-Verify enrolled'],
      ['Where it stands', 'New'],
      ['People', '1 person on record, none asked'],
    ] as const) {
      expect(about).toHaveTextContent(`${label}${text}`);
    }
    expect(within(about).getAllByRole('listitem').map((li) => li.textContent)).toEqual(pylon.queue?.reasons);
    expect(within(about).getByRole('link', { name: 'Software Engineer I, posted 1 day ago' })).toHaveAttribute('href', pylon.role?.url);
    // nothing about Claude, no note, no person
    expect(panel()).not.toHaveTextContent(/Claude/);
    expect(within(panel()).queryByRole('region', { name: 'Note to send' })).not.toBeInTheDocument();
    expect(within(panel()).queryByRole('button', { name: /Confirm|Edit/ })).not.toBeInTheDocument();
  });

  it('S, O and the buttons open LinkedIn search and the careers page in a new tab', async () => {
    const { db } = await open();
    const pylon = rowOf(db, 'Pylon');
    const search = within(panel()).getByRole('link', { name: 'Open LinkedIn search' });
    expect(search).toHaveAttribute('href', pylon.queue?.peopleSearchUrl);
    expect(search).toHaveAttribute('target', '_blank');
    expect(search.getAttribute('rel')).toContain('noopener');
    expect(search).toHaveAttribute('data-variant', 'default');
    expect(search).toHaveAttribute('aria-keyshortcuts', 's');
    expect(within(panel()).getByText('Your saved search opens with S.')).toBeInTheDocument();
    const careers = within(panel()).getByRole('link', { name: 'Careers page' });
    expect(careers).toHaveAttribute('href', pylon.queue?.careersUrl);
    expect(careers.getAttribute('rel')).toContain('noopener');
    expect(careers).toHaveAttribute('aria-keyshortcuts', 'o');

    const opened = vi.spyOn(window, 'open').mockImplementation(() => null);
    press('s');
    press('o');
    expect(opened.mock.calls.map((c) => [c[0], c[1], c[2]])).toEqual([
      [pylon.queue?.peopleSearchUrl, '_blank', 'noopener,noreferrer'],
      [pylon.queue?.careersUrl, '_blank', 'noopener,noreferrer'],
    ]);
  });

  it('C copies the one line for the terminal, and shift+C every company still to do', async () => {
    const writeText = stubClipboard();
    const { db } = await open();
    press('c');
    expect(await screen.findByText('Copied the line for your terminal.')).toBeInTheDocument();
    expect(writeText).toHaveBeenLastCalledWith(`Knock: Pylon (company ${rowOf(db, 'Pylon').company.id}), role: Software Engineer I`);
    press('C', { shiftKey: true });
    expect(await screen.findByText('Copied 6 lines for your terminal.')).toBeInTheDocument();
    const all = writeText.mock.calls.at(-1)?.[0].split('\n') ?? [];
    expect(all).toHaveLength(6);
    expect(all[4]).toBe(`Knock: Trigger.dev (company ${rowOf(db, 'Trigger.dev').company.id})`);
  });

  it('A logs applied on a row with a role, and a row with none says so', async () => {
    const { client } = await open();
    const applied = vi.spyOn(client, 'logApplied');
    press('a');
    expect(await screen.findByText('Logged: applied for Software Engineer I at Pylon.')).toBeInTheDocument();
    fireEvent.click(option(7));
    await screen.findByRole('heading', { level: 2, name: 'Trigger.dev' });
    expect(within(panel()).queryByRole('button', { name: 'Log applied' })).not.toBeInTheDocument();
    toast.dismiss();
    press('a');
    expect(await screen.findByText('No role to apply for.')).toBeInTheDocument();
    await leavePage();
    expect(applied).toHaveBeenCalledTimes(1);
  });

  it('L puts the cursor in Log people; the paste logs the people and the row moves on once it is done', async () => {
    const user = userEvent.setup();
    const { client } = await open({ shell: true });
    const logged = vi.spyOn(client, 'logPeople');
    fireEvent.click(option(7)); // Trigger.dev has no role: the request is its only step
    await screen.findByRole('heading', { level: 2, name: 'Trigger.dev' });
    (document.activeElement as HTMLElement | null)?.blur();
    press('l');
    const box = within(panel()).getByRole('textbox', { name: /Log people you asked/ });
    await waitFor(() => expect(box).toHaveFocus());
    await user.type(box, 'Jane Doe | Senior Software Engineer | Hi Jane{Enter}Omar Diaz | Engineering Manager');
    await user.click(within(panel()).getByRole('button', { name: 'Log people' }));
    expect(await screen.findByText('Logged: 2 people at Trigger.dev.')).toBeInTheDocument();
    expect(option(7)).toHaveAccessibleName('7. Trigger.dev, Done');
    expect(screen.getByText('8 companies, 3 reached')).toBeInTheDocument();
    // done, so the selection moved on to the next company to do
    expect(await screen.findByRole('heading', { level: 2, name: 'Hex' })).toBeInTheDocument();
    await leavePage();
    expect(logged).toHaveBeenCalledTimes(1);
    const [entryId, input] = logged.mock.calls[0] ?? [];
    expect(entryId).toBe(rowOf(buildFixture('queue'), 'Trigger.dev').entryId);
    expect(input?.lines).toBe('Jane Doe | Senior Software Engineer | Hi Jane\nOmar Diaz | Engineering Manager');
    expect(input?.idempotencyKey).toMatch(new RegExp(`^plan:${entryId}:people:`));
  });

  it('an empty paste logs nothing and says what to paste', async () => {
    const user = userEvent.setup();
    const { client } = await open();
    const logged = vi.spyOn(client, 'logPeople');
    await user.click(within(panel()).getByRole('button', { name: 'Log people' }));
    expect(await screen.findByText('Not logged. Paste one line per person: Name | Title | note.')).toBeInTheDocument();
    await leavePage();
    expect(logged).not.toHaveBeenCalled();
  });

  it('a request logged from the terminal shows the row as done on the next read, with no click', async () => {
    const { db, queryClient } = await open();
    const trigger = rowOf(db, 'Trigger.dev');
    expect(option(7)).toHaveAccessibleName('7. Trigger.dev, To do');
    // What the MCP bridge's log_sent leaves in the store; the pulse refetches the plan when it moves.
    const step = trigger.steps[0];
    if (!step) throw new Error('fixture: a request step');
    step.at = db.plan.asOf;
    step.touchId = 4242;
    trigger.state = 'done';
    db.plan.done += 1;
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.plan });
    });
    await waitFor(() => expect(option(7)).toHaveAccessibleName('7. Trigger.dev, Done'));
    expect(screen.getByText('8 companies, 3 reached')).toBeInTheDocument();
  });

  it('Save this search keeps his LinkedIn search for the company, and refuses any other address', async () => {
    const user = userEvent.setup();
    const { client } = await open();
    const saved = vi.spyOn(client, 'saveSearch');
    fireEvent.click(option(8));
    await screen.findByRole('heading', { level: 2, name: 'Hex' });
    const field = within(panel()).getByRole('textbox', { name: 'Save this search' });
    await user.type(field, 'https://evil.example/search/results/people/');
    await user.click(within(panel()).getByRole('button', { name: 'Save' }));
    expect(await screen.findByText(/^Not saved\. Paste the address of a LinkedIn people search/)).toBeInTheDocument();
    expect(saved).not.toHaveBeenCalled();
    const url = 'https://www.linkedin.com/search/results/people/?currentCompany=%5B%2277%22%5D&origin=FACETED_SEARCH';
    await user.clear(field);
    await user.type(field, url);
    await user.click(within(panel()).getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('Saved the search for Hex. S opens it.')).toBeInTheDocument();
    expect(within(panel()).getByRole('link', { name: 'Open LinkedIn search' })).toHaveAttribute('href', url);
    expect(saved).toHaveBeenCalledWith(rowOf(buildFixture('queue'), 'Hex').entryId, url, expect.anything());
  });

  it('In flight is collapsed under the queue and lists each company with where it stands', async () => {
    const user = userEvent.setup();
    await open();
    const toggle = screen.getByRole('button', { name: /In flight/ });
    expect(toggle).toHaveTextContent('In flight5 companies, 1 reply');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('list', { name: 'Companies in flight' })).not.toBeInTheDocument();
    await user.click(toggle);
    const list = screen.getByRole('list', { name: 'Companies in flight' });
    expect(within(list).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'BrowserbaseSomeone replied',
      'Warp4 asked 6 days ago, free again Nov 18',
      'Zed Industries2 asked 5 days ago, room for 2 more, free again Nov 19',
      'Baseten2 asked today, room for 2 more, free again Nov 24',
      'Resend3 asked today, room for 1 more, free again Nov 24',
    ]);
    expect(within(list).getByRole('link', { name: 'Warp' }).getAttribute('href')).toMatch(/^\/companies\/\d+$/);
  });

  it('X carries a company and N opens Not a fit, as on any row', async () => {
    await open();
    press('x');
    expect(await screen.findByText('Carried to tomorrow: Pylon.')).toBeInTheDocument();
    expect(option(3)).toHaveAccessibleName('3. Pylon, Carried over');
    await screen.findByRole('heading', { level: 2, name: 'Inngest' });
    press('n');
    expect(await screen.findByRole('dialog')).toHaveTextContent('Inngest');
  });

  it('the finish line counts companies reached', async () => {
    const db = buildFixture('queue');
    for (const r of db.plan.rows) {
      r.steps = r.steps.map((s) => ({ ...s, at: s.at ?? db.plan.asOf, touchId: s.touchId ?? 1 }));
      r.state = 'done';
    }
    db.plan.done = db.plan.rows.length;
    const client = new FixtureClient(db);
    renderWithApp(<TodayPage />, { route: '/today', client, queryClient: new QueryClient({ defaultOptions: { queries: { retry: false } } }) });
    expect(await screen.findByRole('heading', { level: 2, name: "Today's list is done." })).toBeInTheDocument();
    expect(screen.getByText("8 companies reached. That is everything on today's queue.")).toBeInTheDocument();
    expect(screen.getByText('8 companies, 8 reached')).toBeInTheDocument();
  });
});
