import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { queryKeys, type PlanView } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture, type FixtureDb, type Scenario } from '@/api/fixture/data';
import { isMac } from '@/keyboard/hotkeys';
import { AppShell } from '@/shell/app-shell';
import { renderWithApp } from '@/test/render';
import TodayPage from './index';

const press = (key: string, init: KeyboardEventInit = {}) => fireEvent.keyDown(document.body, { key, ...init });
const sameTick = (...steps: (() => void)[]) =>
  act(() => {
    for (const step of steps) step();
  });

/** The rail's options, in rail order. */
const options = () => within(screen.getByRole('listbox', { name: "Today's people" })).getAllByRole('option');
const option = (n: number) => {
  const o = options()[n - 1];
  if (!o) throw new Error(`No option ${n}`);
  return o;
};
const panel = () => screen.getByRole('region', { name: 'Selected person' });
const panelHeading = () => within(panel()).getByRole('heading', { level: 2 });
const noteRegion = () => within(panel()).getByRole('region', { name: 'Note to send' });
const sidebar = () => document.querySelector<HTMLElement>('[data-slot="sidebar"]') ?? document.body;

function stubClipboard() {
  const writeText = vi.fn<(text: string) => Promise<void>>(async () => {});
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
  return writeText;
}

/** Leaving the page commits what waits in the undo window, at once. */
const leavePage = () =>
  act(() => {
    window.dispatchEvent(new Event('pagehide'));
  });

type Open = { db?: FixtureDb; scenario?: Scenario; shell?: boolean; route?: string; queryClient?: QueryClient };

async function open(o: Open = {}) {
  const db = o.db ?? buildFixture(o.scenario ?? 'default');
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
  const view = renderWithApp(ui, { route: o.route ?? '/today', client, queryClient });
  return { ...view, client, db, queryClient };
}

async function openPlan(o: Open = {}) {
  const view = await open(o);
  await screen.findByRole('heading', { level: 2, name: 'Lena Ortiz' });
  return view;
}

afterEach(() => {
  toast.dismiss();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe('Today as a plan', () => {
  it('lands on Lena Ortiz with her reason, role link, apply link and note', async () => {
    const { client } = await openPlan();
    const lena = (await client.plan()).rows[2];
    if (!lena?.role || !lena.note) throw new Error('fixture: Lena has a role and a note');

    expect(screen.getByRole('heading', { level: 1, name: 'Today' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 2, name: 'Tuesday, November 10' })).toBeInTheDocument();
    expect(screen.getByText('7 people, 2 done')).toBeInTheDocument();
    expect(screen.getByText((_, el) => el?.getAttribute('data-quota') === 'month' && el.textContent === 'Requests this month 52 of 150')).toBeInTheDocument();

    expect(option(3)).toHaveAttribute('aria-selected', 'true');
    expect(option(3)).toHaveAccessibleName('3. Lena Ortiz at Pylon, To do');
    expect(within(panel()).getByText(lena.reason)).toBeInTheDocument();
    expect(within(panel()).getByText((_, el) => el?.tagName === 'SPAN' && el.textContent === 'Software Engineer at Pylon')).toBeInTheDocument();
    expect(within(panel()).getByText('Engineer')).toBeInTheDocument();
    expect(within(panel()).getByRole('link', { name: 'Found on Pylon team page' })).toHaveAttribute('href', 'https://pylon.example/team');
    expect(within(panel()).getByText('Role at your level')).toBeInTheDocument();

    const role = within(panel()).getByRole('link', { name: 'Open role' });
    expect(role).toHaveAttribute('href', lena.role.url);
    expect(role).toHaveAttribute('aria-keyshortcuts', 'o');
    const apply = within(panel()).getByRole('link', { name: 'Apply page' });
    expect(apply).toHaveAttribute('href', lena.applyUrl);
    expect(apply).toHaveAttribute('aria-keyshortcuts', 'a');
    expect(within(noteRegion()).getByText(lena.note.text)).toBeInTheDocument();

    const primary = within(panel()).getByRole('button', { name: 'Log request sent' });
    expect(primary).toHaveAttribute('aria-keyshortcuts', 'r');
    expect(primary).toHaveAttribute('data-variant', 'default');
    expect(within(panel()).getByRole('button', { name: 'Log applied' })).toHaveAttribute('data-variant', 'outline');
    expect(within(panel()).getByRole('link', { name: 'Open profile' })).toHaveAttribute('aria-keyshortcuts', 'l');
  });

  it("the note's fact links are visible before any action", async () => {
    await openPlan();
    const facts = within(noteRegion()).getAllByRole('link');
    expect(facts).toHaveLength(2);
    for (const a of facts) {
      expect(new URL(a.getAttribute('href') ?? '').hostname).toMatch(/\.example$/);
      expect(a).toHaveAttribute('target', '_blank');
    }
  });

  it('r logs the request: the row shows Requested at once, the toast names her, and the primary button becomes Log applied', async () => {
    const { client } = await openPlan({ shell: true });
    const log = vi.spyOn(client, 'logRequest');
    press('r');

    expect(within(panel()).getByText('Requested 10:14')).toBeInTheDocument();
    expect(option(3)).toHaveAccessibleName('3. Lena Ortiz at Pylon, Logged');
    expect(await screen.findByText('Logged: request sent to Lena Ortiz.')).toBeInTheDocument();
    expect(within(panel()).getByRole('button', { name: 'Log applied' })).toHaveAttribute('data-variant', 'default');
    expect(within(panel()).getByRole('button', { name: 'Request logged 10:14' })).toHaveAttribute('data-variant', 'ghost');
    expect(log).not.toHaveBeenCalled();
  });

  it('p then moves the selection to Jonah Park and the sidebar reads 3 of 7', async () => {
    await openPlan({ shell: true });
    press('r');
    press('p');
    expect(await screen.findByRole('heading', { level: 2, name: 'Jonah Park' })).toBeInTheDocument();
    expect(option(4)).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('7 people, 3 done')).toBeInTheDocument();
    await waitFor(() => expect(within(sidebar()).getByRole('link', { name: /^Today/ })).toHaveAccessibleName('Today, 3 of 7 done'));
  });

  it('row stays in place after it is logged', async () => {
    await openPlan();
    const before = options().map((o) => o.getAttribute('data-entry'));
    press('r');
    press('p');
    await screen.findByRole('heading', { level: 2, name: 'Jonah Park' });
    expect(options().map((o) => o.getAttribute('data-entry'))).toEqual(before);
    expect(option(3)).toHaveAccessibleName('3. Lena Ortiz at Pylon, Done');
  });

  it('z undoes the last log inside the window', async () => {
    const { client } = await openPlan({ shell: true });
    const log = vi.spyOn(client, 'logRequest');
    press('r');
    expect(within(panel()).getByText('Requested 10:14')).toBeInTheDocument();
    press('z');
    await waitFor(() => expect(within(panel()).queryByText('Requested 10:14')).not.toBeInTheDocument());
    expect(option(3)).toHaveAccessibleName('3. Lena Ortiz at Pylon, To do');
    expect(within(panel()).getByRole('button', { name: 'Log request sent' })).toHaveAttribute('data-variant', 'default');
    await leavePage();
    expect(log).not.toHaveBeenCalled();
  });

  it('a row with no role has no apply button and p says "No role to apply for."', async () => {
    const db = buildFixture();
    const ines = db.plan.rows[5];
    if (!ines) throw new Error('fixture: row 6');
    ines.role = null;
    ines.applyUrl = null;
    ines.steps = ines.steps.filter((s) => s.kind === 'request');
    const { client } = await openPlan({ db });
    const applied = vi.spyOn(client, 'logApplied');
    fireEvent.click(option(6));
    await screen.findByRole('heading', { level: 2, name: 'Ines Duarte' });
    expect(within(panel()).queryByRole('button', { name: 'Log applied' })).not.toBeInTheDocument();
    expect(within(panel()).queryByRole('link', { name: 'Apply page' })).not.toBeInTheDocument();
    expect(within(panel()).queryByText('Applied')).not.toBeInTheDocument();
    press('p');
    expect(await screen.findByText('No role to apply for.')).toBeInTheDocument();
    await leavePage();
    expect(applied).not.toHaveBeenCalled();
  });

  it('a carried row says where it came from, in grey', async () => {
    await openPlan();
    fireEvent.click(option(6));
    await screen.findByRole('heading', { level: 2, name: 'Ines Duarte' });
    const carried = within(panel()).getByText('Carried over from Monday');
    expect(carried.className).toContain('text-muted-foreground');
    expect(carried.className).not.toMatch(/text-(bad|warn)/);
  });

  it('find someone row shows the search link, c copies the handoff, no note, no log button', async () => {
    const writeText = stubClipboard();
    const { client } = await openPlan();
    const cerebrium = (await client.plan()).rows[4];
    if (!cerebrium?.findSomeone) throw new Error('fixture: row 5 has a handoff');
    fireEvent.click(option(5));
    expect(await screen.findByRole('heading', { level: 2, name: 'Find someone at Cerebrium' })).toBeInTheDocument();

    const search = within(panel()).getByRole('link', { name: 'Open LinkedIn search' });
    expect(search).toHaveAttribute('href', cerebrium.findSomeone.searchUrl);
    expect(search).toHaveAttribute('aria-keyshortcuts', 's');
    expect(search).toHaveAttribute('data-variant', 'default');
    expect(within(panel()).getByText(cerebrium.findSomeone.handoff)).toBeInTheDocument();
    expect(within(panel()).queryByRole('region', { name: 'Note to send' })).not.toBeInTheDocument();
    expect(within(panel()).queryByRole('button', { name: /^Log / })).not.toBeInTheDocument();

    press('c');
    expect(await screen.findByText('Copied the handoff for Claude.')).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledWith(cerebrium.findSomeone.handoff);
  });

  it('r and p on a find someone row log nothing and say how to start', async () => {
    const { client } = await openPlan();
    const request = vi.spyOn(client, 'logRequest');
    const applied = vi.spyOn(client, 'logApplied');
    fireEvent.click(option(5));
    await screen.findByRole('heading', { level: 2, name: 'Find someone at Cerebrium' });
    press('p');
    expect(await screen.findByText('Not logged. Find someone at Cerebrium first. Press S to search.')).toBeInTheDocument();
    toast.dismiss();
    press('r');
    await waitFor(() => expect(screen.getAllByText('Not logged. Find someone at Cerebrium first. Press S to search.').length).toBeGreaterThan(0));
    expect(option(5)).toHaveAccessibleName('5. Find someone at Cerebrium, To do');
    await leavePage();
    expect(request).not.toHaveBeenCalled();
    expect(applied).not.toHaveBeenCalled();
  });

  it('shift+c copies every handoff and toasts "Copied 1 handoff for Claude."', async () => {
    const writeText = stubClipboard();
    const { client } = await openPlan();
    const handoff = (await client.plan()).rows[4]?.findSomeone?.handoff;
    press('C', { shiftKey: true });
    expect(await screen.findByText('Copied 1 handoff for Claude.')).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledWith(handoff);
  });

  it('the email row shows its status and a Review in Outbox link to the item, and no log key acts on it', async () => {
    const { client } = await openPlan();
    const mintlify = (await client.plan()).rows[6];
    const request = vi.spyOn(client, 'logRequest');
    const applied = vi.spyOn(client, 'logApplied');
    fireEvent.click(option(7));
    expect(await screen.findByRole('heading', { level: 2, name: 'Mintlify, by email' })).toBeInTheDocument();
    const review = within(panel()).getByRole('link', { name: 'Review in Outbox' });
    expect(review).toHaveAttribute('href', `/outbox/${mintlify?.emailTouchId}`);
    expect(review).toHaveAttribute('data-variant', 'default');
    expect(await within(panel()).findByText('Draft')).toBeInTheDocument();
    expect(within(panel()).getByText('Marcus Feld')).toBeInTheDocument();

    press('r');
    expect(await screen.findByText('Review this email in the Outbox.')).toBeInTheDocument();
    toast.dismiss();
    press('p');
    await waitFor(() => expect(screen.getAllByText('Review this email in the Outbox.').length).toBeGreaterThan(0));
    await leavePage();
    expect(request).not.toHaveBeenCalled();
    expect(applied).not.toHaveBeenCalled();
  });

  it('copy puts the saved note on the clipboard', async () => {
    const writeText = stubClipboard();
    const { client } = await openPlan();
    const text = (await client.plan()).rows[2]?.note?.text;
    press('c');
    expect(await screen.findByText('Copied the note.')).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledWith(text);
  });

  it('copy warns when a fact in the note has no safe link', async () => {
    const writeText = stubClipboard();
    const db = buildFixture();
    const fact = db.plan.rows[2]?.note?.facts[1];
    if (!fact) throw new Error('fixture: Lena has two facts');
    fact.source.url = 'javascript:alert(1)';
    await openPlan({ db });
    expect(within(noteRegion()).getByText('No link')).toBeInTheDocument();
    press('c');
    expect(await screen.findByText('Copied. One fact has no link: check it before you send.')).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledTimes(1);
  });

  it('c and the Copy button follow the same rule: no copy while a check blocks or the checks are unknown', async () => {
    const writeText = stubClipboard();
    const db = buildFixture();
    const lena = db.plan.rows[2]?.note;
    const jonah = db.plan.rows[3]?.note;
    if (!lena || !jonah) throw new Error('fixture: notes');
    lena.checks = [{ code: 'dash', severity: 'block', title: 'Em or en dash', detail: 'Use a comma.', fix: null, spans: [] }];
    jonah.checks = [];
    await openPlan({ db });

    const copy = within(noteRegion()).getByRole('button', { name: 'Copy' });
    expect(copy).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(copy);
    press('c');
    expect(await screen.findByText('Not copied. 1 check blocks this note. Fix it, then copy.')).toBeInTheDocument();

    fireEvent.click(option(4));
    await screen.findByRole('heading', { level: 2, name: 'Jonah Park' });
    expect(within(noteRegion()).getByRole('button', { name: 'Copy' })).toHaveAttribute('aria-disabled', 'true');
    press('c');
    expect(await screen.findByText('Not copied. The checks have not run yet. Try again in a moment.')).toBeInTheDocument();
    expect(writeText).not.toHaveBeenCalled();
  });

  it('c and y wait while a note save is pending, like the buttons', async () => {
    const writeText = stubClipboard();
    const user = userEvent.setup();
    const { client } = await openPlan();
    const confirm = vi.spyOn(client, 'confirmReason');
    fireEvent.click(option(4));
    await screen.findByRole('heading', { level: 2, name: 'Jonah Park' });
    press('e');
    const text = await within(panel()).findByRole('textbox', { name: 'Note to Jonah Park' });
    await user.clear(text);
    await user.type(text, 'Hi Jonah, a short note.');
    await user.click(within(panel()).getByRole('button', { name: 'Save' }));

    expect(within(noteRegion()).getByRole('button', { name: 'Copy' })).toHaveAttribute('aria-disabled', 'true');
    press('c');
    expect(await screen.findByText('Not copied. The note is saving, and the checks run again first.')).toBeInTheDocument();
    press('y');
    expect(await screen.findByText('Not confirmed. The note is saving. Press Y again once it is saved.')).toBeInTheDocument();
    expect(writeText).not.toHaveBeenCalled();
    await leavePage();
    expect(confirm).not.toHaveBeenCalled();
  });

  it('edits survive moving rows, and Save sends the text and the line and shows the rescanned checks', async () => {
    const user = userEvent.setup();
    const { client } = await openPlan();
    const save = vi.spyOn(client, 'saveNote');
    press('e');
    const text = await within(panel()).findByRole('textbox', { name: 'Note to Lena Ortiz' });
    await user.clear(text);
    await user.type(text, "Hi Lena, I'd love to ask about billing.");
    act(() => text.blur());

    press('j');
    await screen.findByRole('heading', { level: 2, name: 'Jonah Park' });
    press('k');
    await screen.findByRole('heading', { level: 2, name: 'Lena Ortiz' });
    const again = within(panel()).getByRole('textbox', { name: 'Note to Lena Ortiz' });
    expect(again).toHaveValue("Hi Lena, I'd love to ask about billing.");

    await user.click(within(panel()).getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('Saved the note for Lena Ortiz.')).toBeInTheDocument();
    // Focus never drops to the page: it lands on the person's name.
    await waitFor(() => expect(panelHeading()).toHaveFocus());
    await leavePage();
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0]?.[1]).toMatchObject({
      text: "Hi Lena, I'd love to ask about billing.",
      reasonLine: 'Her team shipped usage billing in October (blog post).',
    });
    expect(await within(noteRegion()).findByText('1 check blocks this note.')).toBeInTheDocument();
    expect(within(noteRegion()).getByText('Banned phrase')).toBeInTheDocument();
  });

  it('moving rows mounts a fresh note status region', async () => {
    await openPlan();
    const before = within(noteRegion()).getByRole('status');
    press('j');
    await screen.findByRole('heading', { level: 2, name: 'Jonah Park' });
    press('k');
    await screen.findByRole('heading', { level: 2, name: 'Lena Ortiz' });
    expect(within(noteRegion()).getByRole('status')).not.toBe(before);
  });

  it('y confirms the proposed reason line', async () => {
    const { client } = await openPlan();
    const confirm = vi.spyOn(client, 'confirmReason');
    fireEvent.click(option(4));
    await screen.findByRole('heading', { level: 2, name: 'Jonah Park' });
    expect(option(4)).toHaveAccessibleName('4. Jonah Park at Inngest, To do, your line to confirm');
    expect(within(noteRegion()).getByText('He leads the team that shipped durable steps in November.')).toBeInTheDocument();
    // Ruling 13: Confirm line is the filled button until the line is his.
    expect(within(noteRegion()).getByRole('button', { name: 'Confirm line' })).toHaveAttribute('data-variant', 'default');
    expect(within(panel()).getByRole('button', { name: 'Log request sent' })).toHaveAttribute('data-variant', 'outline');

    press('y');
    expect(await screen.findByText('Confirmed your line.')).toBeInTheDocument();
    expect(within(noteRegion()).queryByRole('button', { name: 'Confirm line' })).not.toBeInTheDocument();
    expect(within(panel()).getByRole('button', { name: 'Log request sent' })).toHaveAttribute('data-variant', 'default');
    await leavePage();
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
  });

  it('editing a proposed line shows it read-only above the editor, and saving a new line makes it his', async () => {
    const user = userEvent.setup();
    const { client } = await openPlan();
    fireEvent.click(option(4));
    await screen.findByRole('heading', { level: 2, name: 'Jonah Park' });
    press('e');
    const line = await within(panel()).findByRole('textbox', { name: 'Why them, in your words' });
    const proposed = within(panel()).getByText('He leads the team that shipped durable steps in November.');
    expect(proposed.closest('[data-proposed]')).toHaveTextContent('Claude proposed');
    expect(proposed.closest('textarea, input')).toBeNull();
    await user.clear(line);
    await user.type(line, 'His durable steps post fixed a bug in my job runner.');
    await user.click(within(panel()).getByRole('button', { name: 'Save' }));
    await leavePage();
    await waitFor(async () => expect((await client.plan()).rows[3]?.note?.reasonState).toBe('written'));
    expect(await within(noteRegion()).findByText('His durable steps post fixed a bug in my job runner.')).toBeInTheDocument();
  });

  it('x carries a row: the toast names the company, the rail says Carried over, and done does not change', async () => {
    await openPlan();
    fireEvent.click(option(4));
    await screen.findByRole('heading', { level: 2, name: 'Jonah Park' });
    press('x');
    expect(await screen.findByText('Carried to tomorrow: Inngest.')).toBeInTheDocument();
    expect(option(4)).toHaveAccessibleName('4. Jonah Park at Inngest, Carried over');
    expect(screen.getByText('7 people, 2 done')).toBeInTheDocument();
    // The selection moves to the next row to do.
    expect(await screen.findByRole('heading', { level: 2, name: 'Find someone at Cerebrium' })).toBeInTheDocument();

    press('x');
    expect(await screen.findByText('Carried to tomorrow: Cerebrium.')).toBeInTheDocument();
    expect(option(5)).toHaveAccessibleName('5. Find someone at Cerebrium, Carried over');
    expect(screen.getByText('7 people, 2 done')).toBeInTheDocument();
  });

  it('a carried row keeps its place and says so in the panel', async () => {
    await openPlan();
    press('x');
    await screen.findByRole('heading', { level: 2, name: 'Jonah Park' });
    fireEvent.click(option(3));
    await screen.findByRole('heading', { level: 2, name: 'Lena Ortiz' });
    expect(within(panel()).getByText('Carried to tomorrow. It is first on tomorrow\'s plan.')).toBeInTheDocument();
    expect(within(panel()).queryByRole('button', { name: 'Log request sent' })).not.toBeInTheDocument();
  });

  it('a done row shows its times and where to change a log', async () => {
    await openPlan();
    fireEvent.click(option(1));
    await screen.findByRole('heading', { level: 2, name: 'Dana Whitfield' });
    expect(within(panel()).getByText(/^Requested \d+:\d\d$/)).toBeInTheDocument();
    expect(within(panel()).getByText(/^Done at \d+:\d\d\. To change a log, open/)).toBeInTheDocument();
    expect(within(panel()).getByRole('link', { name: "Resend's page" })).toHaveAttribute('href', expect.stringMatching(/^\/companies\/\d+$/));
    expect(within(panel()).queryByRole('button', { name: /^Log / })).not.toBeInTheDocument();
  });

  it('n opens the not a fit dialog with the four reasons', async () => {
    await openPlan();
    press('n');
    const dialog = await screen.findByRole('dialog', { name: 'Remove Pylon for good?' });
    expect(within(dialog).getAllByRole('radio')).toHaveLength(4);
    expect(within(dialog).getByLabelText('Not a fit for me')).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Already applied or in touch another way')).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Bigger than it looks')).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Other')).toBeInTheDocument();
  });

  it('Remove in the not a fit dialog removes the company at once and moves on', async () => {
    const { client } = await openPlan();
    const remove = vi.spyOn(client, 'removeCompany');
    press('n');
    const dialog = await screen.findByRole('dialog', { name: 'Remove Pylon for good?' });
    fireEvent.click(within(dialog).getByLabelText('Bigger than it looks'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));
    expect(await screen.findByText('Removed Pylon. Find it on the No-go list.')).toBeInTheDocument();
    await waitFor(() => expect(remove).toHaveBeenCalledTimes(1));
    expect(remove.mock.calls[0]?.[1]).toBe('bigger_than_it_looks');
    await expect(remove.mock.results[0]?.value).resolves.toBeUndefined();
    expect(await screen.findByRole('heading', { level: 2, name: 'Jonah Park' })).toBeInTheDocument();
  });

  it('a removed company leaves the plan at once, as the API leaves it off, and nothing is logged for it', async () => {
    const { client } = await openPlan();
    const request = vi.spyOn(client, 'logRequest');
    const applied = vi.spyOn(client, 'logApplied');
    const skip = vi.spyOn(client, 'skipRow');
    const pylon = (await client.plan()).rows[2]?.entryId;
    press('n');
    const dialog = await screen.findByRole('dialog', { name: 'Remove Pylon for good?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));
    // At once, before the commit lands: the row is off the rail and out of the day's total.
    expect(options()).toHaveLength(6);
    expect(options().map((o) => o.textContent).join(' ')).not.toMatch(/Pylon/);
    expect(screen.getByText('6 people, 2 done')).toBeInTheDocument();
    expect(await screen.findByRole('heading', { level: 2, name: 'Jonah Park' })).toBeInTheDocument();
    // Once the commit and the refetch land, the API says the same, with the positions counted again.
    await waitFor(async () => expect((await client.plan()).rows.map((r) => r.company.name)).not.toContain('Pylon'));
    await waitFor(() => expect(option(3)).toHaveAccessibleName(/^3\. Jonah Park at Inngest/));
    press('r');
    await leavePage();
    // r logged Jonah's request, never Pylon's; nothing was carried.
    expect(request.mock.calls.every(([entryId]) => entryId !== pylon)).toBe(true);
    expect(applied).not.toHaveBeenCalled();
    expect(skip).not.toHaveBeenCalled();
  });

  it('the finish line is reached when the last row to do is removed', async () => {
    const db = buildFixture();
    for (const r of db.plan.rows.slice(2, 6)) r.state = 'skipped';
    await open({ db });
    expect(await screen.findByRole('heading', { level: 2, name: 'Mintlify, by email' })).toBeInTheDocument();
    const finish = () => document.querySelector('[data-finish]');
    expect(finish()).toHaveAttribute('data-done', 'false');
    press('n');
    const dialog = await screen.findByRole('dialog', { name: 'Remove Mintlify for good?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(finish()).toHaveAttribute('data-done', 'true'));
    expect(finish()).toHaveAttribute('aria-current', 'step');
    // Mintlify is off the plan, as the API leaves a removed company off it.
    expect(options()).toHaveLength(6);
    expect(options().map((o) => o.textContent).join(' ')).not.toMatch(/Mintlify/);
  });

  it('Not a fit after a step is logged is refused up front, as the API keeps a company with a request out', async () => {
    const { client } = await openPlan();
    const remove = vi.spyOn(client, 'removeCompany');
    press('r');
    expect(await screen.findByText('Logged: request sent to Lena Ortiz.')).toBeInTheDocument();
    press('n');
    expect(await screen.findByText('Not removed. Pylon already has a request or an application logged. To stop, close it on its page.')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.click(within(panel()).getByRole('button', { name: 'Not a fit' }));
    await act(async () => {});
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await leavePage();
    expect(remove).not.toHaveBeenCalled();
  });

  it('n acts only on a row to do, like its button', async () => {
    await openPlan();
    fireEvent.click(option(1));
    await screen.findByRole('heading', { level: 2, name: 'Dana Whitfield' });
    expect(within(panel()).queryByRole('button', { name: 'Not a fit' })).not.toBeInTheDocument();
    press('n');
    await act(async () => {});
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('coming back to a row with a kept edit does not take focus into the editor', async () => {
    const user = userEvent.setup();
    await openPlan();
    press('e');
    const text = await within(panel()).findByRole('textbox', { name: 'Note to Lena Ortiz' });
    await waitFor(() => expect(text).toHaveFocus());
    await user.type(text, ' More.');
    act(() => text.blur());
    press('j');
    await screen.findByRole('heading', { level: 2, name: 'Jonah Park' });
    press('k');
    await screen.findByRole('heading', { level: 2, name: 'Lena Ortiz' });
    const again = within(panel()).getByRole('textbox', { name: 'Note to Lena Ortiz' });
    await act(async () => {
      await new Promise((r) => requestAnimationFrame(() => r(null)));
    });
    expect(again).not.toHaveFocus();
    // Pressing e again is a start, so it focuses the kept edit.
    press('e');
    await waitFor(() => expect(again).toHaveFocus());
    expect((again as HTMLTextAreaElement).value).toMatch(/ More\.$/);
  });

  it('while an edit differs from the saved note, Copy waits and c says to save first', async () => {
    const writeText = stubClipboard();
    const user = userEvent.setup();
    await openPlan();
    press('e');
    const text = await within(panel()).findByRole('textbox', { name: 'Note to Lena Ortiz' });
    // An open editor with no change copies as before.
    expect(within(noteRegion()).getByRole('button', { name: 'Copy' })).toHaveAttribute('aria-disabled', 'false');
    await user.type(text, ' More.');
    const copyButton = within(noteRegion()).getByRole('button', { name: 'Copy' });
    expect(copyButton).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(copyButton);
    act(() => text.blur());
    press('c');
    const save = isMac() ? 'Cmd+S' : 'Ctrl+S';
    expect(await screen.findByText(`Not copied. Save your edit first (${save}), then copy.`)).toBeInTheDocument();
    expect(writeText).not.toHaveBeenCalled();
  });

  it('weekend scenario shows "No plan on weekends." with tomorrow\'s preview', async () => {
    await open({ scenario: 'weekend' });
    expect(await screen.findByText(/^No plan on weekends\./)).toBeInTheDocument();
    expect(screen.getByText('Monday: 7 people')).toBeInTheDocument();
    expect(screen.getByText(/Aiden Frost/)).toBeInTheDocument();
    expect(screen.getByText('and 4 more')).toBeInTheDocument();
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('job_failed shows the plan job banner with its error, and the API words for the day', async () => {
    await open({ scenario: 'job_failed' });
    expect(await screen.findByText('Could not read the company store: database is locked')).toBeInTheDocument();
    // The plan's job is the plan job (#15), as GET /api/plan reports it, not the old daily list.
    expect(screen.getByText(/^Plan failed/)).toBeInTheDocument();
    expect(screen.getByText("Today's plan did not build. The plan job said: Could not read the company store: database is locked")).toBeInTheDocument();
  });

  it('a day the plan job has not built yet says so in the API words, with nobody to work on', async () => {
    await open({ scenario: 'empty' });
    expect(await screen.findByText("Today's plan is not built yet. It builds at 8:00 on weekdays.")).toBeInTheDocument();
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('an empty built plan says who to add and how', async () => {
    const db = buildFixture();
    db.plan.rows = [];
    db.plan.total = 0;
    db.plan.done = 0;
    await open({ db });
    expect(await screen.findByText('Nobody is on today\'s plan.')).toBeInTheDocument();
    expect(screen.getByText(/side terminal/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open all proposed companies' })).toHaveAttribute('href', '/proposals');
  });

  it('links to the proposals under the rail', async () => {
    await openPlan();
    expect(screen.getByRole('link', { name: 'All proposed companies' })).toHaveAttribute('href', '/proposals');
  });

  it('no spinner after first load', async () => {
    await openPlan();
    const busy = () => document.querySelectorAll('[aria-busy="true"], [role="progressbar"]').length;
    expect(busy()).toBe(0);
    press('r');
    expect(within(panel()).getByText('Requested 10:14')).toBeInTheDocument();
    expect(busy()).toBe(0);
    await leavePage();
    expect(busy()).toBe(0);
  });

  it('polls keep the selected row', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await openPlan({ queryClient });
    const plan = queryClient.getQueryData<PlanView>(queryKeys.plan);
    if (!plan) throw new Error('no plan');
    // A poll lands with the rows in another order and Lena's row finished from another tab.
    const rows = [...plan.rows].reverse().map((r) => (r.position === 3 ? { ...r, state: 'done' as const, steps: r.steps.map((s) => ({ ...s, at: plan.asOf })) } : r));
    await act(async () => {
      queryClient.setQueryData<PlanView>(queryKeys.plan, { ...plan, rows, done: 3 });
    });
    expect(await screen.findByText('7 people, 3 done')).toBeInTheDocument();
    expect(panelHeading()).toHaveTextContent('Lena Ortiz');
    expect(screen.getByRole('option', { selected: true })).toHaveAccessibleName('3. Lena Ortiz at Pylon, Done');
  });

  it('keys act on the row he just moved to', async () => {
    const { client } = await openPlan();
    const log = vi.spyOn(client, 'logRequest');
    sameTick(
      () => press('j'),
      () => press('r'),
    );
    expect(await screen.findByText('Logged: request sent to Jonah Park.')).toBeInTheDocument();
    expect(screen.queryByText('Logged: request sent to Lena Ortiz.')).not.toBeInTheDocument();
    expect(option(3)).toHaveAccessibleName('3. Lena Ortiz at Pylon, To do');
    await leavePage();
    await waitFor(() => expect(log).toHaveBeenCalledTimes(1));
    expect(log.mock.calls[0]?.[0]).toBe((await client.plan()).rows[3]?.entryId);
  });

  it('a press on a row whose person changed under it is refused, and says what to do', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { client } = await openPlan({ queryClient });
    const log = vi.spyOn(client, 'logRequest');
    const plan = queryClient.getQueryData<PlanView>(queryKeys.plan);
    const lena = plan?.rows[2];
    if (!plan || !lena?.person) throw new Error('no plan');
    const wen = { ...lena.person, person: { ...lena.person.person, id: 99_001, name: 'Wen Zhao' } };
    const next: PlanView = { ...plan, rows: plan.rows.map((r) => (r.entryId === lena.entryId ? { ...r, person: wen } : r)) };
    // The poll lands and the key arrives before the view has drawn it.
    sameTick(
      () => queryClient.setQueryData<PlanView>(queryKeys.plan, next),
      () => press('r'),
    );
    expect(await screen.findByText("Not logged. The person on Pylon's row changed to Wen Zhao. Check the row, then press R again.")).toBeInTheDocument();
    await leavePage();
    expect(log).not.toHaveBeenCalled();
  });

  it('a log whose person changed inside the undo window is refused, and says what to do', async () => {
    const { db } = await openPlan();
    press('r');
    await screen.findByText('Logged: request sent to Lena Ortiz.');
    const lena = db.plan.rows[2];
    if (!lena?.person) throw new Error('fixture: Lena');
    lena.person = { ...lena.person, person: { ...lena.person.person, id: 99_002, name: 'Wen Zhao' } };
    await leavePage();
    expect(await screen.findByText("Not logged. That person is not on this row. Check Pylon's row, then log it again.")).toBeInTheDocument();
  });

  it('the initial selection is pinned: it stays on Lena when her row finishes elsewhere', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await openPlan({ queryClient });
    const plan = queryClient.getQueryData<PlanView>(queryKeys.plan);
    if (!plan) throw new Error('no plan');
    await act(async () => {
      queryClient.setQueryData<PlanView>(queryKeys.plan, {
        ...plan,
        rows: plan.rows.map((r) => (r.position === 3 ? { ...r, state: 'done' as const, steps: r.steps.map((s) => ({ ...s, at: plan.asOf })) } : r)),
        done: 3,
      });
    });
    expect(await screen.findByText('7 people, 3 done')).toBeInTheDocument();
    expect(panelHeading()).toHaveTextContent('Lena Ortiz');
    expect(within(panel()).getByText(/^Done at /)).toBeInTheDocument();
    expect(option(3)).toHaveAttribute('aria-selected', 'true');
  });

  it('a selection in the URL wins over the first row to do', async () => {
    const plan = buildFixture().plan;
    const jonah = plan.rows[3]?.entryId;
    await open({ route: `/today?sel=${jonah}` });
    expect(await screen.findByRole('heading', { level: 2, name: 'Jonah Park' })).toBeInTheDocument();
  });
});
