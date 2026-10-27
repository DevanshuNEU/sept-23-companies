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
 * Today as the top picks (#9 company-first ruling, P2; top picks 2026-10-26), on the `queue` sample: 8 picks, Pylon
 * (new opening, 6 hours ago) and Cerebrium (new opening, yesterday) first, Pylon first to do (with his saved search),
 * Resend and Baseten reached, Turso carried from Monday, 5 companies in flight, 12 companies on the bench.
 */

const press = (key: string, init: KeyboardEventInit = {}) => fireEvent.keyDown(document.body, { key, ...init });
const options = () => within(screen.getByRole('listbox', { name: "Today's picks" })).getAllByRole('option');
const option = (n: number) => {
  const o = options()[n - 1];
  if (!o) throw new Error(`No option ${n}`);
  return o;
};
const panel = () => screen.getByRole('region', { name: 'Selected company' });
const progress = () => screen.getByRole('meter', { name: 'Picks reached today' });
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
  it('lands on the first pick to do, new openings first, and the panel leads with why today', async () => {
    const { db } = await open();
    const pylon = rowOf(db, 'Pylon');
    expect(screen.getByText('8 picks')).toBeInTheDocument();
    expect(screen.getByRole('meter', { name: 'Picks reached today' })).toHaveAttribute('aria-valuetext', '2 of 8 reached');
    expect(options().map((o) => o.querySelector('b')?.textContent)).toEqual(
      ['Pylon', 'Cerebrium', 'Resend', 'Baseten', 'Inngest', 'Turso', 'Trigger.dev', 'Hex'],
    );
    expect(option(1)).toHaveAttribute('aria-selected', 'true');
    expect(option(1)).toHaveAccessibleName('1. Pylon, To do, new opening, 6 hours ago');
    expect(option(1)).toHaveTextContent('New opening, 6 hours agoSWE I role: Software Engineer I');
    expect(option(2)).toHaveTextContent('New opening, yesterdaySWE I role: Member of Technical Staff');
    expect(option(3)).toHaveAccessibleName('3. Resend, Done');
    expect(option(3)).toHaveTextContent('New grad role, posted 4 days ago');
    expect(option(3).querySelector('[data-fresh]')).toBeNull();

    // Why today comes first: the opening and its age, why it should answer, the visa position.
    const why = within(panel()).getByRole('region', { name: 'Why today' });
    expect(panel().firstElementChild?.firstElementChild?.children[1]).toBe(why); // right under the name
    expect(why).toHaveTextContent('New opening, 6 hours ago');
    expect(within(why).getByRole('link', { name: 'Software Engineer I' })).toHaveAttribute('href', pylon.role?.url);
    expect(why).toHaveTextContent('SWE I role, first seen 6 hours ago');
    // The reasons are small chips: the new opening, E-Verify, sponsorship, size, hiring, then the ranker's own (each once).
    expect(within(why).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'New opening, 6 hours ago', 'E-Verify enrolled', 'Says it sponsors', '30 people (estimate), Series A',
      '3 new engineering roles in the last 30 days', 'Open role at your level', 'New opening today',
    ]);
    const about = within(panel()).getByLabelText('About Pylon');
    expect(about).toHaveTextContent('Where it standsNew');
    // nothing about Claude, no note, no person
    expect(panel()).not.toHaveTextContent(/Claude/);
    expect(within(panel()).queryByRole('region', { name: 'Note to send' })).not.toBeInTheDocument();
    expect(within(panel()).queryByRole('button', { name: /Confirm|Edit/ })).not.toBeInTheDocument();
  });

  it('the judge says what the company builds in why today and its fit on the rail, and says when it has not judged', async () => {
    const { db } = await open();
    const pylon = rowOf(db, 'Pylon');
    expect(pylon.queue?.judgeLine).toBe(
      'Judge: builds a support platform for B2B teams, with AI agents that answer tickets. Fit 82. Risk: no E-Verify statement on its pages.',
    );
    const why = within(panel()).getByRole('region', { name: 'Why today' });
    // His one-line reason in a quote, with its risk; the fit is a chip on the rail and under the name.
    expect(within(why).getByText('Agent work on support tickets is close to the work he wants.')).toBeInTheDocument();
    expect(within(why).getByText('Risk: No E-Verify statement on its pages.')).toBeInTheDocument();
    expect(option(1)).toHaveTextContent('Fit 82');
    expect(within(panel()).getByText('Fit 82')).toBeInTheDocument();
    // Inngest has no judgment: the panel says so plainly, and the rail says nothing.
    expect(option(5)).not.toHaveTextContent('Judge');
    fireEvent.click(option(5));
    await screen.findByRole('heading', { level: 2, name: 'Inngest' });
    const inngest = within(panel()).getByRole('region', { name: 'Why today' });
    expect(within(inngest).getByText('Judge: not judged yet.')).toBeInTheDocument();
  });

  it('the three research links and S, O open in a new tab; the app never fetches LinkedIn', async () => {
    const { db } = await open();
    const pylon = rowOf(db, 'Pylon');
    const search = within(panel()).getByRole('link', { name: 'Open LinkedIn search' });
    expect(search).toHaveAttribute('href', pylon.queue?.peopleSearchUrl);
    expect(search).toHaveAttribute('target', '_blank');
    expect(search.getAttribute('rel')).toContain('noopener');
    expect(search).toHaveAttribute('data-variant', 'default');
    expect(search).toHaveAttribute('aria-keyshortcuts', 's');
    expect(within(panel()).getByText('Your saved search opens with S')).toBeInTheDocument();
    const research = within(panel()).getByRole('group', { name: 'Research' });
    const careers = within(research).getByRole('link', { name: 'Careers' });
    expect(careers).toHaveAttribute('href', pylon.queue?.careersUrl);
    expect(careers).toHaveAttribute('aria-keyshortcuts', 'o');
    expect(within(research).getByRole('link', { name: 'Website' })).toHaveAttribute('href', pylon.queue?.websiteUrl);
    expect(within(research).getByRole('link', { name: 'LinkedIn page' })).toHaveAttribute(
      'href', 'https://www.linkedin.com/search/results/companies/?keywords=Pylon',
    );
    for (const a of within(research).getAllByRole('link')) expect(a.getAttribute('rel')).toContain('noopener');

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
    expect(all[0]).toBe(`Knock: Pylon (company ${rowOf(db, 'Pylon').company.id}), role: Software Engineer I`);
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
    expect(progress()).toHaveAttribute('aria-valuetext', '3 of 8 reached');
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
    await user.click(within(panel()).getByRole('button', { name: /Log people you asked/ }));
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
    expect(progress()).toHaveAttribute('aria-valuetext', '3 of 8 reached');
  });

  it('Save this search keeps his LinkedIn search for the company, and refuses any other address', async () => {
    const user = userEvent.setup();
    const { client } = await open();
    const saved = vi.spyOn(client, 'saveSearch');
    fireEvent.click(option(8));
    await screen.findByRole('heading', { level: 2, name: 'Hex' });
    await user.click(within(panel()).getByRole('button', { name: /Save this search/ }));
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

  it('X carries a pick; N and P both open Not now, never the old Not a fit dialog', async () => {
    await open();
    press('x');
    expect(await screen.findByText('Carried to tomorrow: Pylon.')).toBeInTheDocument();
    expect(option(1)).toHaveAccessibleName('1. Pylon, Carried over, new opening, 6 hours ago');
    await screen.findByRole('heading', { level: 2, name: 'Cerebrium' });
    press('n');
    expect(within(panel()).getByRole('group', { name: 'Not now for Cerebrium' })).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(panel()).not.toHaveTextContent(/No-go list|Never show/);
    fireEvent.click(within(panel()).getByRole('button', { name: 'Keep it' }));
    expect(within(panel()).queryByRole('group', { name: 'Not now for Cerebrium' })).not.toBeInTheDocument();
    press('p');
    expect(within(panel()).getByRole('group', { name: 'Not now for Cerebrium' })).toBeInTheDocument();
  });

  it('the finish line counts the picks handled', async () => {
    const db = buildFixture('queue');
    for (const r of db.plan.rows) {
      r.steps = r.steps.map((s) => ({ ...s, at: s.at ?? db.plan.asOf, touchId: s.touchId ?? 1 }));
      r.state = 'done';
    }
    const last = db.plan.rows.at(-1);
    if (last) last.state = 'skipped';
    db.plan.done = db.plan.rows.length - 1;
    db.plan.passed = 2;
    const client = new FixtureClient(db);
    renderWithApp(<TodayPage />, { route: '/today', client, queryClient: new QueryClient({ defaultOptions: { queries: { retry: false } } }) });
    expect(await screen.findByRole('heading', { level: 2, name: "Today's list is done." })).toBeInTheDocument();
    expect(screen.getByText('8 of 8 handled: 7 reached, Hex carries to tomorrow. You passed on 2 for now.')).toBeInTheDocument();
    expect(screen.getByText('8 picks, 1 carried over, 2 passed')).toBeInTheDocument();
    expect(progress()).toHaveAttribute('aria-valuetext', '7 of 8 reached');
  });
});

describe('Not now (top picks)', () => {
  it('P then a number passes with that reason, and the bench head slides into the place, with Undo', async () => {
    const { client, db } = await open();
    const passed = vi.spyOn(client, 'passPick');
    const pylon = rowOf(db, 'Pylon');
    press('p');
    const chips = within(panel()).getByRole('group', { name: 'Not now for Pylon' });
    expect(chips).toHaveTextContent('It comes back in 14 days, or sooner with a new opening. It is not a no-go.');
    expect(within(chips).getAllByRole('button').map((b) => b.getAttribute('aria-keyshortcuts'))).toEqual(
      ['1', '2', '3', '4', '5', '6', '7', '8', 'enter', null],
    );
    expect(within(chips).getByRole('button', { name: /Not a fit right now/ })).toBeInTheDocument();
    press('2');
    expect(await screen.findByText('Passed. Added Retell AI.')).toBeInTheDocument();
    expect(option(1)).toHaveAttribute('data-provisional', 'true');
    expect(option(1)).toHaveTextContent('Retell AI');
    expect(option(1)).toHaveTextContent('Coming in from the bench');
    expect(screen.queryByText('Pylon', { selector: 'b *, b' })).toBeNull();
    // the selection moved on to the next pick to do
    expect(await screen.findByRole('heading', { level: 2, name: 'Cerebrium' })).toBeInTheDocument();
    expect(passed).not.toHaveBeenCalled(); // still inside the undo window
    await leavePage();
    expect(passed).toHaveBeenCalledWith(pylon.entryId, { reason: 'too_big', idempotencyKey: `plan:${pylon.entryId}:pass` }, pylon.revision, expect.anything());
    await waitFor(() => expect(options().some((o) => o.getAttribute('data-provisional'))).toBe(false));
    expect(option(1)).toHaveTextContent('Retell AI');
    expect(option(1)).toHaveTextContent('New opening, 3 hours ago');
    expect(options()).toHaveLength(8);
  });

  it('Enter or Esc passes with no reason; Z undoes and nothing is sent', async () => {
    const { client } = await open({ shell: true });
    const passed = vi.spyOn(client, 'passPick');
    press('p');
    press('Escape');
    expect(await screen.findByText('Passed. Added Retell AI.')).toBeInTheDocument();
    press('z');
    await waitFor(() => expect(option(1)).toHaveAccessibleName('1. Pylon, To do, new opening, 6 hours ago'));
    expect(options().some((o) => o.getAttribute('data-provisional'))).toBe(false);
    fireEvent.click(option(1));
    await screen.findByRole('heading', { level: 2, name: 'Pylon' });
    press('p');
    press('Enter');
    await leavePage();
    expect(passed).toHaveBeenCalledTimes(1);
    expect(passed.mock.calls[0]?.[1].reason).toBeNull();
  });

  it('a pick he already reached stays; one with only an application passes', async () => {
    const db = buildFixture('queue');
    const hex = rowOf(db, 'Hex');
    hex.steps = hex.steps.map((s) => (s.kind === 'apply' ? { ...s, at: db.plan.asOf, touchId: 77 } : s));
    const inngest = rowOf(db, 'Inngest');
    inngest.steps = inngest.steps.map((s) => (s.kind === 'request' ? { ...s, at: db.plan.asOf, touchId: 78 } : s));
    await open({ db });
    fireEvent.click(option(5));
    await screen.findByRole('heading', { level: 2, name: 'Inngest' });
    press('p');
    expect(await screen.findByText('You already reached Inngest today, so it stays on the list.')).toBeInTheDocument();
    expect(within(panel()).queryByRole('group', { name: /Not now/ })).not.toBeInTheDocument();
    fireEvent.click(option(8));
    await screen.findByRole('heading', { level: 2, name: 'Hex' });
    press('p');
    expect(within(panel()).getByRole('group', { name: 'Not now for Hex' })).toBeInTheDocument();
    press('7');
    expect(await screen.findByText('Passed. Added Retell AI.')).toBeInTheDocument();
  });

  it('the keys on a pick still coming in say so', async () => {
    await open();
    press('p');
    press('1');
    await screen.findByRole('heading', { level: 2, name: 'Cerebrium' });
    fireEvent.click(option(1));
    await screen.findByRole('heading', { level: 2, name: 'Retell AI' });
    press('a');
    expect(await screen.findByText(/Retell AI joins today's list once your Not now is saved/)).toBeInTheDocument();
  });
});

describe('the bench (top picks)', () => {
  const bench = () => screen.getByRole('dialog', { name: /More options/ });

  it('B opens More options: new early career openings first, then other new ones, then by rank', async () => {
    await open();
    press('b');
    const list = within(await screen.findByRole('dialog', { name: /More options/ })).getByRole('list', { name: 'More options' });
    const names = within(list).getAllByRole('listitem').map((li) => li.querySelector('a')?.textContent);
    expect(names).toEqual(['Retell AI', 'Railway', 'Temporal', 'Graphite', 'Supabase', 'Fly.io', 'Weaviate', 'Clerk', 'Tailscale', 'Sourcegraph', 'Render', 'PlanetScale']);
    expect(names).not.toContain('Crusoe'); // passed on Nov 7: cooling off
    const items = within(list).getAllByRole('listitem');
    expect(items[0]).toHaveTextContent('New opening, 3 hours ago');
    expect(items[1]).toHaveTextContent('You applied on Oct 20; this is a new role.');
    expect(items[2]).toHaveTextContent('New opening, 10 hours agoSWE II role: Software Engineer II');
    expect(items[3]?.querySelector('[data-fresh]')).toBeNull();
    expect(bench()).toHaveTextContent(/Room to add \d+ more companies this week\. New openings come first\./);
    press('b');
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /More options/ })).not.toBeInTheDocument());
  });

  it('Add to today appends the company after the last pick', async () => {
    const user = userEvent.setup();
    const { client, db } = await open();
    const added = vi.spyOn(client, 'addFromBench');
    await user.click(screen.getByRole('button', { name: /More options/ }));
    const graphite = within(bench()).getAllByRole('listitem')[3];
    if (!graphite) throw new Error('no Graphite');
    await user.click(within(graphite).getByRole('button', { name: 'Add to today' }));
    expect(await screen.findByText('Added Graphite to today.')).toBeInTheDocument();
    expect(options()).toHaveLength(9);
    expect(option(9)).toHaveTextContent('Graphite');
    await leavePage();
    const id = buildFixture('queue').bench.companies.find((b) => b.company.name === 'Graphite')?.company.id;
    expect(db.bench.companies.some((b) => b.company.id === id)).toBe(false);
    expect(added).toHaveBeenCalledWith(id, { idempotencyKey: `bench:${id}:add` }, expect.anything());
  });

  it('with no room this week Add to today is off and says why', async () => {
    const db = buildFixture('queue');
    db.bench.room = 0;
    db.bench.roomText = "No room to add more this week: the 7-day limit is taken by today's picks and the requests already sent.";
    await open({ db });
    press('b');
    const first = within(await screen.findByRole('dialog', { name: /More options/ })).getAllByRole('listitem')[0];
    expect(within(first as HTMLElement).getByRole('button', { name: 'Add to today' })).toBeDisabled();
    expect(bench()).toHaveTextContent('No room to add more this week');
  });

  it('a new opening that reaches the bench mid-day says so in a toast', async () => {
    const { db, queryClient } = await open();
    await waitFor(() => expect(queryClient.getQueryData(queryKeys.bench)).toBeDefined());
    const fresh = structuredClone(db.bench.companies[0]);
    if (!fresh) throw new Error('fixture: a bench company');
    fresh.company = { ...fresh.company, id: 99_001, name: 'Northbeam Labs' };
    db.bench.companies.unshift(fresh);
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.bench });
    });
    expect(await screen.findByText('New opening at Northbeam Labs')).toBeInTheDocument();
  });
});
