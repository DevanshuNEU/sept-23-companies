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
  it("counts requests over the last 7 days, and the tooltip names LinkedIn's weekly limit", async () => {
    const user = userEvent.setup();
    await openPlan();
    await user.hover(screen.getByRole('button', { name: 'About this limit' }));
    expect(await screen.findByRole('tooltip')).toHaveTextContent(
      "The plan uses 150 of LinkedIn's 200 a week. 31 of 200 are used in the last 7 days, counting requests sent outside the plan.",
    );
  });

  it('lands on Lena Ortiz with her reason, role link, apply link and note', async () => {
    const { client } = await openPlan();
    const lena = (await client.plan()).rows[2];
    if (!lena?.role || !lena.note) throw new Error('fixture: Lena has a role and a note');

    expect(screen.getByRole('heading', { level: 1, name: 'Today' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 2, name: 'Tuesday, November 10' })).toBeInTheDocument();
    expect(screen.getByText('7 people, 2 done')).toBeInTheDocument();
    expect(screen.getByText((_, el) => el?.getAttribute('data-quota') === 'week' && el.textContent === 'Requests this week 26 of 150')).toBeInTheDocument();
    expect(screen.queryByText(/Requests this month/)).toBeNull();

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
    expect(apply).not.toHaveAttribute('aria-keyshortcuts');  // A logs applied (company first)
    expect(within(noteRegion()).getByText(lena.note.text)).toBeInTheDocument();

    const primary = within(panel()).getByRole('button', { name: 'Log request sent' });
    expect(primary).toHaveAttribute('aria-keyshortcuts', 'r');
    expect(primary).toHaveAttribute('data-variant', 'default');
    expect(within(panel()).getByRole('button', { name: 'Log applied' })).toHaveAttribute('data-variant', 'outline');
    expect(within(panel()).getByRole('button', { name: 'Log applied' })).toHaveAttribute('aria-keyshortcuts', 'a');
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

  it('a then moves the selection to Jonah Park and the sidebar reads 3 of 7', async () => {
    await openPlan({ shell: true });
    press('r');
    press('a');
    expect(await screen.findByRole('heading', { level: 2, name: 'Jonah Park' })).toBeInTheDocument();
    expect(option(4)).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('7 people, 3 done')).toBeInTheDocument();
    await waitFor(() => expect(within(sidebar()).getByRole('link', { name: /^Today/ })).toHaveAccessibleName('Today, 3 of 7 done'));
  });

  it('row stays in place after it is logged', async () => {
    await openPlan();
    const before = options().map((o) => o.getAttribute('data-entry'));
    press('r');
    press('a');
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

  it('a row with no role has no apply button and a says "No role to apply for."', async () => {
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
    press('a');
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

  it('the one company row on a people plan is drawn by the company panel (the queue itself: company-queue.test.tsx)', async () => {
    const writeText = stubClipboard();
    const { client } = await openPlan();
    const cerebrium = (await client.plan()).rows[4];
    if (!cerebrium?.queue) throw new Error('fixture: row 5 is a company row');
    fireEvent.click(option(5));
    const region = await screen.findByRole('region', { name: 'Selected company' });
    expect(within(region).getByRole('heading', { level: 2, name: 'Cerebrium' })).toBeInTheDocument();
    expect(option(5)).toHaveAccessibleName('5. Cerebrium, To do');
    expect(region).not.toHaveTextContent(/Claude/);
    expect(within(region).queryByRole('region', { name: 'Note to send' })).not.toBeInTheDocument();
    press('c');
    expect(await screen.findByText('Copied the line for your terminal.')).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledWith(cerebrium.queue.terminalLine);
    toast.dismiss();
    press('C', { shiftKey: true });
    expect(await screen.findByText('Copied 1 line for your terminal.')).toBeInTheDocument();
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
    press('a');
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

  describe('Enter on a person: open the profile, then copy the note', () => {
    it('opens the profile inside the key press, then copies, and logs nothing', async () => {
      const writeText = stubClipboard();
      const open = vi.spyOn(window, 'open').mockReturnValue(null);
      const { client } = await openPlan();
      const lena = (await client.plan()).rows[2];
      const request = vi.spyOn(client, 'logRequest');
      press('Enter');
      // Synchronous: the window opens in the same call as the key press, so a popup blocker allows it.
      expect(open).toHaveBeenCalledTimes(1);
      expect(open).toHaveBeenCalledWith(lena?.person?.person.linkedinUrl, '_blank', 'noopener,noreferrer');
      expect(writeText).toHaveBeenCalledTimes(1);
      expect(writeText).toHaveBeenCalledWith(lena?.note?.text);
      expect(open.mock.invocationCallOrder[0]).toBeLessThan(writeText.mock.invocationCallOrder[0] ?? 0);
      expect(await screen.findByText('Opened the profile. Copied the note.')).toBeInTheDocument();
      // R stays his own explicit log.
      expect(request).not.toHaveBeenCalled();
      expect(panelHeading()).toHaveTextContent('Lena Ortiz');
    });

    it('obeys the checks that block c: nothing opens and nothing is copied', async () => {
      const writeText = stubClipboard();
      const open = vi.spyOn(window, 'open').mockReturnValue(null);
      const db = buildFixture();
      const lena = db.plan.rows[2]?.note;
      if (!lena) throw new Error('fixture: Lena has a note');
      lena.checks = [{ code: 'dash', severity: 'block', title: 'Em or en dash', detail: 'Use a comma.', fix: null, spans: [] }];
      await openPlan({ db });
      press('Enter');
      expect(await screen.findByText('Not copied. 1 check blocks this note. Fix it, then copy.')).toBeInTheDocument();
      expect(open).not.toHaveBeenCalled();
      expect(writeText).not.toHaveBeenCalled();
    });

    it('waits for an unsaved edit like c does', async () => {
      const writeText = stubClipboard();
      const open = vi.spyOn(window, 'open').mockReturnValue(null);
      const user = userEvent.setup();
      await openPlan();
      press('e');
      const text = await within(panel()).findByRole('textbox', { name: 'Note to Lena Ortiz' });
      await user.type(text, ' more');
      act(() => (document.activeElement as HTMLElement | null)?.blur());
      press('Enter');
      expect(await screen.findByText(/^Not copied\. Save your edit first/)).toBeInTheDocument();
      expect(open).not.toHaveBeenCalled();
      expect(writeText).not.toHaveBeenCalled();
    });

    it('a profile that is not a web address is never opened', async () => {
      const writeText = stubClipboard();
      const open = vi.spyOn(window, 'open').mockReturnValue(null);
      const db = buildFixture();
      const person = db.plan.rows[2]?.person?.person;
      if (!person) throw new Error('fixture: Lena has a profile');
      person.linkedinUrl = 'javascript:alert(1)';
      await openPlan({ db });
      press('Enter');
      expect(open).not.toHaveBeenCalled();
      expect(writeText).not.toHaveBeenCalled();
    });

    it('the Copy and open chip does the same on a click, with Enter in its hint', async () => {
      const writeText = stubClipboard();
      const open = vi.spyOn(window, 'open').mockReturnValue(null);
      await openPlan();
      const chip = within(panel()).getByRole('button', { name: /^Copy and open/ });
      expect(chip).toHaveAttribute('aria-keyshortcuts', 'enter');
      fireEvent.click(chip);
      expect(open).toHaveBeenCalledTimes(1);
      expect(writeText).toHaveBeenCalledTimes(1);
      expect(await screen.findByText('Opened the profile. Copied the note.')).toBeInTheDocument();
    });

    it('c and l keep doing one thing each', async () => {
      const writeText = stubClipboard();
      const open = vi.spyOn(window, 'open').mockReturnValue(null);
      await openPlan();
      press('l');
      expect(open).toHaveBeenCalledTimes(1);
      expect(writeText).not.toHaveBeenCalled();
      press('c');
      expect(await screen.findByText('Copied the note.')).toBeInTheDocument();
      expect(open).toHaveBeenCalledTimes(1);
    });
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

  it('his own note over a line Claude proposed asks nothing, and y and Copy treat it as his (#9, 2026-10-26)', async () => {
    const user = userEvent.setup();
    const writeText = stubClipboard(); // after userEvent, which brings its own clipboard
    const { client } = await openPlan();
    const confirm = vi.spyOn(client, 'confirmReason');
    fireEvent.click(option(4));
    await screen.findByRole('heading', { level: 2, name: 'Jonah Park' });
    press('e');
    const body = await within(panel()).findByRole('textbox', { name: 'Note to Jonah Park' });
    await user.clear(body);
    await user.type(body, 'Hi Jonah, I build agent infra and would like to hear how your team ships.');
    await user.click(within(panel()).getByRole('button', { name: 'Save' }));
    await leavePage();
    await waitFor(async () => expect((await client.plan()).rows[3]?.note?.savedBy).toBe('devanshu'));
    expect((await client.plan()).rows[3]?.note?.reasonState).toBe('proposed');
    await waitFor(() => expect(option(4)).toHaveAccessibleName('4. Jonah Park at Inngest, To do'));
    expect(within(noteRegion()).queryByText('Is this why you are writing to them? Confirm it or write your own.')).toBeNull();
    expect(within(noteRegion()).queryByRole('button', { name: 'Confirm line' })).toBeNull();
    expect(within(noteRegion()).queryByText('He leads the team that shipped durable steps in November.')).toBeNull();
    expect(within(panel()).getByRole('button', { name: 'Log request sent' })).toHaveAttribute('data-variant', 'default');

    press('y');
    expect(await screen.findByText('Nothing to confirm. You wrote this note, so it needs no line.')).toBeInTheDocument();
    const copy = within(noteRegion()).getByRole('button', { name: 'Copy' });
    expect(copy).toHaveAttribute('aria-disabled', 'false');
    fireEvent.click(copy);
    expect(await screen.findByText('Copied the note.')).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledWith('Hi Jonah, I build agent infra and would like to hear how your team ships.');
    await leavePage();
    expect(confirm).not.toHaveBeenCalled();
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
    expect(await screen.findByRole('heading', { level: 2, name: 'Cerebrium' })).toBeInTheDocument();

    press('x');
    expect(await screen.findByText('Carried to tomorrow: Cerebrium.')).toBeInTheDocument();
    expect(option(5)).toHaveAccessibleName('5. Cerebrium, Carried over');
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

  it('n opens Not now with its reason chips, never the old remove dialog (his ruling, 2026-10-26)', async () => {
    const { client } = await openPlan();
    const remove = vi.spyOn(client, 'removeCompany');
    press('n');
    const chips = screen.getByRole('group', { name: 'Not now for Pylon' });
    expect(within(chips).getAllByRole('button')).toHaveLength(12);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    press('6');
    expect(await screen.findByText('Passed. Added Retell AI.')).toBeInTheDocument();
    expect(await screen.findByRole('heading', { level: 2, name: 'Jonah Park' })).toBeInTheDocument();
    expect(options().map((o) => o.textContent).join(' ')).not.toMatch(/Lena Ortiz/);
    await leavePage();
    expect(remove).not.toHaveBeenCalled();
  });

  it('Not now offers Wrong company and Wrong location next to the other reasons', async () => {
    await openPlan();
    press('n');
    const chips = screen.getByRole('group', { name: 'Not now for Pylon' });
    expect(within(chips).getByRole('button', { name: /Wrong company/ })).toHaveAttribute('aria-keyshortcuts', '9');
    expect(within(chips).getByRole('button', { name: /Wrong location/ })).toHaveAttribute('aria-keyshortcuts', '0');
    expect(chips).toHaveTextContent('Wrong company and Wrong location fix the data and are not a Not now.');
  });

  it.each([
    ['9', 'wrong_company'],
    ['0', 'wrong_location'],
  ])('pressing %s passes at once with %s', async (key, reason) => {
    const { client } = await openPlan();
    const passed = vi.spyOn(client, 'passPick');
    press('n');
    press(key);
    expect(await screen.findByText('Passed. Added Retell AI.')).toBeInTheDocument();
    await leavePage();
    expect(passed).toHaveBeenCalledWith(expect.any(Number), expect.objectContaining({ reason }), expect.any(Number), expect.anything());
    expect(passed.mock.calls[0]?.[1]).not.toHaveProperty('note');
    // the posting the pick showed him goes along: the lead opening can change before the pass commits
    const sent = passed.mock.calls[0];
    const shown = (await client.plan()).rows.find((r) => r.entryId === sent?.[0])?.queue?.opening?.postingId;
    if (shown !== undefined) expect(sent?.[1]).toMatchObject({ postingId: shown });
    else expect(sent?.[1]).not.toHaveProperty('postingId');
  });

  it.each([
    ['8', 'other'],
    ['4', 'role'],
  ])('%s (%s) asks for his words first and passes with them on Enter', async (key, reason) => {
    const { client } = await openPlan();
    const passed = vi.spyOn(client, 'passPick');
    press('n');
    press(key);
    const why = screen.getByRole('textbox', { name: 'Why' });
    expect(passed).not.toHaveBeenCalled();
    fireEvent.change(why, { target: { value: '  Brazil only  ' } });
    fireEvent.submit(why.closest('form')!);
    expect(await screen.findByText('Passed. Added Retell AI.')).toBeInTheDocument();
    await leavePage();
    expect(passed).toHaveBeenCalledWith(expect.any(Number), expect.objectContaining({ reason, note: 'Brazil only' }), expect.any(Number), expect.anything());
  });

  it('Enter with nothing typed still passes with the reason, and Esc goes back to the chips', async () => {
    const { client } = await openPlan();
    const passed = vi.spyOn(client, 'passPick');
    press('n');
    press('8');
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Why' }), { key: 'Escape' });
    expect(screen.queryByRole('textbox', { name: 'Why' })).not.toBeInTheDocument();
    expect(passed).not.toHaveBeenCalled();
    press('8');
    fireEvent.submit(screen.getByRole('textbox', { name: 'Why' }).closest('form')!);
    expect(await screen.findByText('Passed. Added Retell AI.')).toBeInTheDocument();
    await leavePage();
    expect(passed.mock.calls[0]?.[1]).toMatchObject({ reason: 'other' });
    expect(passed.mock.calls[0]?.[1]).not.toHaveProperty('note');
  });

  it('Not now after the request is logged is refused up front: he reached them', async () => {
    const { client } = await openPlan();
    const passed = vi.spyOn(client, 'passPick');
    press('r');
    expect(await screen.findByText('Logged: request sent to Lena Ortiz.')).toBeInTheDocument();
    press('n');
    expect(await screen.findByText('You already reached Pylon today, so it stays on the list.')).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: /Not now/ })).not.toBeInTheDocument();
    await leavePage();
    expect(passed).not.toHaveBeenCalled();
  });

  it('n acts only on a row to do, like its button', async () => {
    await openPlan();
    fireEvent.click(option(1));
    await screen.findByRole('heading', { level: 2, name: 'Dana Whitfield' });
    expect(within(panel()).queryByRole('button', { name: 'Not now' })).not.toBeInTheDocument();
    press('n');
    await act(async () => {});
    expect(screen.queryByRole('group', { name: /Not now/ })).not.toBeInTheDocument();
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

  it('a weekend with nothing carried reads as two plain lines and no list', async () => {
    const db = buildFixture('weekend');
    db.plan.tomorrow = { total: 25, text: "Tomorrow's plan builds at 08:00. Room for 25 people.", preview: [] };
    await open({ db });
    expect(await screen.findByText('No plan on weekends.')).toBeInTheDocument();
    expect(screen.getByText("Tomorrow's plan builds at 08:00. Room for 25 people.")).toBeInTheDocument();
    expect(screen.queryByRole('list')).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/below|and \d+ more/i);
  });

  it('weekend scenario shows "No plan on weekends." with tomorrow\'s draft', async () => {
    await open({ scenario: 'weekend' });
    expect(await screen.findByText(/^No plan on weekends\./)).toBeInTheDocument();
    const draft = within(screen.getByRole('region', { name: "Tomorrow's draft" }));
    expect(draft.getByText("Monday's plan builds at 08:00. Room for 25 people. A draft of who it would propose is below.")).toBeInTheDocument();
    expect(draft.getByText('Rebuilt at 08:00')).toBeInTheDocument();
    const lines = draft.getAllByRole('listitem');
    expect(lines).toHaveLength(10);
    expect(lines[0]).toHaveTextContent(/Aiden Frost at Hex, Software Engineer, New Grad\s*LinkedIn$/);
    expect(lines[3]).toHaveTextContent(/Julia Brandt at Neon\s*Email$/);
    expect(lines[9]).toHaveTextContent(/Zed Industries, Software Engineer, Early Career\s*Find someone$/);
    expect(draft.getByText('and 3 more')).toBeInTheDocument();
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
    expect(screen.getByText(/you find the people on LinkedIn and write your own notes/)).toBeInTheDocument();
    expect(screen.queryByText(/Claude/)).not.toBeInTheDocument();
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
