import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient } from '@tanstack/react-query';
import { ApiError } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture } from '@/api/fixture/data';
import { toast } from 'sonner';
import { renderWithApp } from '@/test/render';
import { AppShell } from '@/shell/app-shell';
import BrowsePage from './index';

/*
 * The job list (#156, the /browse page), on the sample: 82 postings from 11 companies, newest first by when each was
 * posted. At your level: 62 (22 in the last 30 days, 8 in the last 7, each counting the 2 with no date); at higher
 * levels: 20 (6 in the last 30 days). He reads every posting himself: Pass hides one, Reached marks its company as
 * reached.
 */

const press = (key: string, init: KeyboardEventInit = {}) => fireEvent.keyDown(document.body, { key, ...init });
const list = () => screen.getByRole('list', { name: 'Jobs' });
const rows = () => Array.from(list().querySelectorAll<HTMLElement>(':scope > li[data-job]'));
const undated = () => screen.getByRole('list', { name: 'Date unknown' });
const row = (id: number) => document.querySelector<HTMLElement>(`li[data-job="${id}"]`) as HTMLElement;
const titles = () => rows().map((r) => within(r).getAllByRole('link')[0]?.textContent);
const companies = () => rows().map((r) => r.getAttribute('data-company'));
const search = () => screen.getByRole('searchbox', { name: 'Search by company name' });
const leavePage = () =>
  act(() => {
    window.dispatchEvent(new Event('pagehide'));
  });
const settle = (ms: number) =>
  act(async () => {
    if (vi.isFakeTimers()) await vi.advanceTimersByTimeAsync(ms);
    else await Promise.resolve();
  });
const count = (text: string | RegExp) => screen.findByText(text);

async function open(o: { shell?: boolean } = {}) {
  const client = new FixtureClient(buildFixture('queue'));
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
  await screen.findByRole('heading', { level: 1, name: 'Jobs' });
  await waitFor(() => expect(rows().length).toBeGreaterThan(0));
  return { ...view, client, queryClient };
}

/** The row's reached tag ("Reached Nov 10"), not the Mark reached button beside it. */
const REACHED_TAG = /Reached [A-Z][a-z]{2} \d/;

describe('the Jobs page', () => {
  // Toasts live in a global store: one test's toast must not be another's Undo.
  beforeEach(() => {
    act(() => {
      toast.dismiss();
    });
  });

  it('says how many at your level in the last 30 days, and each row says what the posting is', async () => {
    await open();
    expect(await count('22 at your level in the last 30 days')).toBeInTheDocument();
    expect(rows()).toHaveLength(20);
    const first = rows()[0] as HTMLElement;
    const link = within(first).getByRole('link', { name: 'Software Engineer, New Grad' });
    expect(link).toHaveAttribute('href', 'https://jobs.example/retellai/9000');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'));
    expect(first).toHaveTextContent('new grad');
    expect(first).toHaveTextContent('Retell AI');
    expect(first).toHaveTextContent('posted 10:08, 6 min ago');
    expect(first).toHaveTextContent('45 people');
    expect(first).toHaveTextContent('San Francisco, CA');
    expect(first).toHaveTextContent('E-Verify');
    expect(first).toHaveTextContent('YC');
    expect(within(first).getByRole('button', { name: /^Pass/ })).toBeInTheDocument();
    expect(within(first).getByRole('button', { name: /^Mark reached/ })).toBeInTheDocument();
    // One word per meaning: the key legend names A as the button does, and Done stays the closed companies' page.
    const legend = screen.getByText('pass').closest('p') as HTMLElement;
    expect(legend).toHaveTextContent(/A\s*mark reached/);
    expect(legend).not.toHaveTextContent(/done/i);
  });

  it('shows the company\'s H-1B tag as a plain badge with the years as its hover text, and no tag without a list', async () => {
    await open();
    const tagged = within(row(9000)).getByText('H-1B 14, rising');
    expect(tagged).toHaveAttribute('title', expect.stringContaining('FY2026 7'));
    expect(within(row(9001)).getByText('No H-1B on record')).toBeInTheDocument(); // Pylon
    expect(row(9002)).not.toHaveTextContent(/H-1B/); // Cerebrium: the sample has no list line for it
  });

  it('lists newest first by when each job was posted, and has no score or ranking text', async () => {
    await open();
    expect(rows().map((r) => r.getAttribute('data-job')).slice(0, 5)).toEqual(['9000', '9001', '9002', '9004', '9005']);
    expect(document.body).not.toHaveTextContent(/\bfit\b|why it ranked|score/i);
  });

  it('shows the posted time to the minute', async () => {
    await open();
    expect(row(9000)).toHaveTextContent('posted 10:08, 6 min ago'); // the board's time, today
    expect(row(9001)).toHaveTextContent('posted Nov 9, 9:13'); // the board's time, yesterday
    expect(row(9002)).toHaveTextContent('posted Nov 8'); // a board that gives only the date
    expect(row(9002)).not.toHaveTextContent(/posted Nov 8, \d/);
  });

  it('shows first seen when the board gives no time', async () => {
    await open();
    expect(row(9004)).toHaveTextContent('first seen Nov 6, 10:10');
    expect(row(9004)).not.toHaveTextContent('posted');
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Any time' }));
    // Rows from before list times keep their day, as the list showed it then.
    await waitFor(() => expect(row(9064)).toHaveTextContent('first seen Aug 26'));
    expect(row(9064)).not.toHaveTextContent(/Aug 26, \d/);
    expect(row(9060)).toHaveTextContent('posted Aug 30');
    expect(row(9060)).not.toHaveTextContent(/Aug 30, \d/);
  });

  it('tags a late find with its gap', async () => {
    await open();
    expect(within(row(9004)).getByText('Found after a 3 day gap')).toBeInTheDocument();
    expect(row(9009)).toHaveTextContent('first seen'); // seen 30 min after the last read: no tag
    expect(row(9009)).not.toHaveTextContent('Found after');
    expect(row(9000)).not.toHaveTextContent('Found after');
  });

  it('tags a repost with its old date', async () => {
    await open();
    expect(within(row(9001)).getByText('Reposted, first seen Oct 2')).toBeInTheDocument();
    expect(row(9000)).not.toHaveTextContent('Reposted');
  });

  it('groups rows without a date at the end', async () => {
    const user = userEvent.setup();
    await open();
    const heading = screen.getByRole('heading', { level: 2, name: 'Date unknown' });
    expect(heading).toHaveTextContent('Date unknown');
    expect(screen.getByText('These boards give no posting date, and Knock found these jobs on its first read.')).toBeInTheDocument();
    const late = Array.from(undated().querySelectorAll<HTMLElement>(':scope > li[data-job]'));
    expect(late.map((r) => r.getAttribute('data-job'))).toEqual(['9080', '9081']);
    expect(late[0]).toHaveTextContent('first seen Nov 9');
    expect(late[1]).toHaveTextContent('first seen Nov 8');
    expect(rows().map((r) => r.getAttribute('data-job'))).not.toContain('9080');
    // The group comes after every dated row, and the arrows walk on into it.
    expect(list().compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    for (let i = 0; i < rows().length; i++) press('ArrowDown');
    expect(late[0]).toHaveAttribute('aria-current', 'true');
    // Every window keeps them.
    await user.click(screen.getByRole('button', { name: '7 days' }));
    expect(await count('8 at your level in the last 7 days')).toBeInTheDocument();
    await waitFor(() => expect(undated().querySelectorAll('li[data-job]')).toHaveLength(2));
  });

  it('names a big company, an unknown size and the flags a company carries', async () => {
    const user = userEvent.setup();
    await open();
    await user.click(screen.getByRole('button', { name: 'Any time' }));
    await waitFor(() => expect(rows()).toHaveLength(50));
    const of = (name: string) => rows().find((r) => r.getAttribute('data-company') === name) as HTMLElement;
    expect(of('Baseten')).toHaveTextContent('Big company, 640 people');
    expect(of('Resend')).toHaveTextContent('300 people'); // 300 is not over 300
    expect(of('Resend')).not.toHaveTextContent('Big company');
    expect(of('Axiom')).toHaveTextContent('Size not known');
    expect(of('Axiom')).not.toHaveTextContent('Big company');
    expect(of('Granite Systems')).toHaveTextContent('Big company, size not known'); // guessed big: no size on file
    expect(of('Helix')).toHaveTextContent('Says no sponsorship');
    expect(of('Cerebrium')).toHaveTextContent('Reached Nov 3');
    expect(of('Cerebrium')).toHaveTextContent('18 people');
  });

  it('the two tabs ask for the other level and say how many each holds', async () => {
    const user = userEvent.setup();
    await open();
    const yours = screen.getByRole('tab', { name: /^Your level/ });
    const higher = screen.getByRole('tab', { name: /^Higher levels/ });
    expect(yours).toHaveAttribute('aria-selected', 'true');
    expect(yours).toHaveTextContent('22');
    expect(higher).toHaveTextContent('6');
    await user.click(higher);
    expect(await count('6 at higher levels in the last 30 days')).toBeInTheDocument();
    expect(rows()).toHaveLength(6);
    expect(titles()).toContain('Senior Software Engineer');
    expect(titles()).not.toContain('Software Engineer, New Grad');
    expect(higher).toHaveAttribute('aria-selected', 'true');
  });

  it('Other roles holds every listed role that is not software, with its own count (#250)', async () => {
    const user = userEvent.setup();
    await open();
    const others = screen.getByRole('tab', { name: /^Other roles/ });
    expect(others).toHaveTextContent('3');
    expect(titles()).not.toContain('Data Scientist I');
    await user.click(others);
    expect(await count('3 in other roles in the last 30 days')).toBeInTheDocument();
    expect(titles()).toEqual(['Data Scientist I', 'Design Engineer', 'DevOps Engineer']);
    expect(others).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: /^Your level/ })).toHaveTextContent('22');
  });

  it('tags a company that may be a staffing firm and a posting made for a client (#257)', async () => {
    await open();
    expect(row(9006)).toHaveTextContent('Maybe a staffing firm');
    expect(row(9005)).toHaveTextContent('Posted for a client');
    expect(row(9005)).not.toHaveTextContent('Maybe a staffing firm');
    expect(row(9000)).not.toHaveTextContent('Posted for a client');
  });

  it('a window chip changes what is asked, and Any time pages 50 at a time', async () => {
    const user = userEvent.setup();
    await open();
    const group = screen.getByRole('group', { name: 'Posted within' });
    expect(within(group).getByRole('button', { name: '30 days' })).toHaveAttribute('aria-pressed', 'true');
    await user.click(within(group).getByRole('button', { name: '7 days' }));
    expect(await count('8 at your level in the last 7 days')).toBeInTheDocument();
    expect(rows()).toHaveLength(6);
    await user.click(within(group).getByRole('button', { name: 'Any time' }));
    expect(await count('62 at your level')).toBeInTheDocument();
    await waitFor(() => expect(rows()).toHaveLength(50));
    await user.click(screen.getByRole('button', { name: 'Show more' }));
    await waitFor(() => expect(rows()).toHaveLength(60));
    expect(new Set(rows().map((r) => r.getAttribute('data-job'))).size).toBe(60);
    expect(screen.queryByRole('button', { name: 'Show more' })).not.toBeInTheDocument();
  });

  it('the size filter keeps big companies, small ones or the ones with no size', async () => {
    const user = userEvent.setup();
    await open();
    const group = screen.getByRole('group', { name: 'Company size' });
    await user.click(within(group).getByRole('button', { name: 'Big' }));
    await waitFor(() => expect(new Set(companies())).toEqual(new Set(['Baseten', 'Granite Systems', 'Helix', 'Kestrel']))); // Granite is big by the guess
    await user.click(within(group).getByRole('button', { name: 'Not known' }));
    await waitFor(() => expect(new Set(companies())).toEqual(new Set(['Axiom'])));
    await user.click(within(group).getByRole('button', { name: 'Small' }));
    await waitFor(() => expect(companies()).not.toContain('Axiom'));
    expect(companies()).not.toContain('Baseten');
    expect(companies()).not.toContain('Granite Systems');
    expect(companies()).toContain('Resend');
    await user.click(within(group).getByRole('button', { name: 'Any size' }));
    await waitFor(() => expect(rows()).toHaveLength(20));
  });

  it('Not contacted yet drops a company he already contacted; the flag still shows without it', async () => {
    const user = userEvent.setup();
    await open();
    expect(companies()).toContain('Cerebrium');
    const toggle = screen.getByRole('button', { name: 'Not contacted yet' });
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    await waitFor(() => expect(companies()).not.toContain('Cerebrium'));
  });

  it('a company search narrows the list by name', async () => {
    const user = userEvent.setup();
    await open();
    await user.type(search(), 'retell');
    await waitFor(() => expect(new Set(companies())).toEqual(new Set(['Retell AI'])));
  });

  it('the hour windows ask for 1h, 6h and 24h and say so in the headline', async () => {
    const user = userEvent.setup();
    const { client } = await open();
    const asked = vi.spyOn(client, 'jobs');
    const group = screen.getByRole('group', { name: 'Posted within' });
    expect(within(group).getAllByRole('button').map((b) => b.textContent)).toEqual(['1 hour', '6 hours', '24 hours', '7 days', '30 days', '60 days', '90 days', 'Any time']);
    await user.click(within(group).getByRole('button', { name: '1 hour' }));
    await waitFor(() => expect(asked).toHaveBeenLastCalledWith(expect.objectContaining({ window: '1h' })));
    expect(await screen.findByText(/at your level in the last hour$/)).toBeInTheDocument();
    await user.click(within(group).getByRole('button', { name: '6 hours' }));
    await waitFor(() => expect(asked).toHaveBeenLastCalledWith(expect.objectContaining({ window: '6h' })));
    expect(await screen.findByText(/at your level in the last 6 hours$/)).toBeInTheDocument();
    for (const r of rows()) expect(r.closest('[aria-labelledby="date-unknown-h"]')).toBeNull();
  });

  it('the level chips start on Any level, combine, and Any level clears them', async () => {
    const user = userEvent.setup();
    const { client } = await open();
    const asked = vi.spyOn(client, 'jobs');
    const group = screen.getByRole('group', { name: 'Level' });
    expect(within(group).getAllByRole('button').map((b) => b.textContent)).toEqual(['Any level', 'New grad', 'Early career', 'SDE 1', 'SDE 2', 'Entry level', 'Level not stated']);
    expect(within(group).getByRole('button', { name: 'Any level' })).toHaveAttribute('aria-pressed', 'true');
    await user.click(within(group).getByRole('button', { name: 'Early career' }));
    await waitFor(() => expect(asked).toHaveBeenLastCalledWith(expect.objectContaining({ levels: ['early_career'] })));
    await waitFor(() => expect(rows().length).toBeGreaterThan(0));
    await waitFor(() => expect(rows().every((r) => r.textContent?.includes('early career'))).toBe(true));
    expect(within(group).getByRole('button', { name: 'Any level' })).toHaveAttribute('aria-pressed', 'false');
    await user.click(within(group).getByRole('button', { name: 'New grad' }));
    await waitFor(() => expect(asked).toHaveBeenLastCalledWith(expect.objectContaining({ levels: ['new_grad', 'early_career'] })));
    await waitFor(() => expect(rows().some((r) => r.textContent?.includes('new grad'))).toBe(true));
    expect(rows().every((r) => /new grad|early career/.test(r.textContent ?? ''))).toBe(true);
    await user.click(within(group).getByRole('button', { name: 'Early career' }));
    await waitFor(() => expect(asked).toHaveBeenLastCalledWith(expect.objectContaining({ levels: ['new_grad'] })));
    await user.click(within(group).getByRole('button', { name: 'Any level' }));
    await waitFor(() => expect(asked).toHaveBeenLastCalledWith(expect.objectContaining({ levels: [] })));
    await waitFor(() => expect(rows()).toHaveLength(20));
  });

  it('an empty list with a level chip on says to clear the level', async () => {
    const user = userEvent.setup();
    await open();
    await user.click(within(screen.getByRole('group', { name: 'Level' })).getByRole('button', { name: 'SDE 1' }));
    await user.type(search(), 'no such company');
    expect(await screen.findByText('Widen the window, clear the level, or clear the search.')).toBeInTheDocument();
  });

  it('an empty list says what to widen, naming only what is on', async () => {
    const user = userEvent.setup();
    await open();
    await user.click(screen.getByRole('button', { name: '7 days' }));
    await user.click(screen.getByRole('button', { name: 'Not contacted yet' }));
    await user.type(search(), 'cerebrium');
    expect(await screen.findByText('No jobs at your level in the last 7 days.')).toBeInTheDocument();
    expect(screen.getByText('Widen the window, clear the search, or turn off Not contacted yet.')).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Jobs' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Any time' }));
    await waitFor(() => expect(screen.queryByText('No jobs at your level in the last 7 days.')).not.toBeInTheDocument());
    expect(await screen.findByText('No jobs at your level.')).toBeInTheDocument();
    expect(screen.getByText('Clear the search or turn off Not contacted yet.')).toBeInTheDocument();
  });

  it('an empty tab points to the other one when it holds some', async () => {
    const user = userEvent.setup();
    await open();
    await user.click(screen.getByRole('button', { name: '7 days' }));
    await user.type(search(), 'resend');
    expect(await screen.findByText('No jobs at your level in the last 7 days.')).toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: 'Show 1 at higher levels' }));
    expect(await count('1 at higher levels in the last 7 days')).toBeInTheDocument();
    expect(new Set(companies())).toEqual(new Set(['Resend']));
  });

  it('says plainly when the jobs did not load, and Try again asks once more', async () => {
    const user = userEvent.setup();
    const client = new FixtureClient(buildFixture('queue'));
    const jobs = vi.spyOn(client, 'jobs').mockRejectedValueOnce(new ApiError('network', 'Cannot reach the Knock server'));
    renderWithApp(<BrowsePage />, { route: '/browse', client });
    expect(await screen.findByRole('alert')).toHaveTextContent('The jobs did not load. Nothing was changed.');
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(rows().length).toBeGreaterThan(0));
    expect(jobs).toHaveBeenCalledTimes(2);
  });

  describe('Pass', () => {
    const passButton = (i = 0) => within(rows()[i] as HTMLElement).getByRole('button', { name: /^Pass/ });

    it('takes the row off at once, says so with an Undo, and sends nothing until the window closes', async () => {
      const user = userEvent.setup();
      const { client } = await open();
      const sent = vi.spyOn(client, 'passJob');
      const second = titles()[1];
      await user.click(passButton());
      expect(rows()).toHaveLength(19);
      expect(titles()[0]).toBe(second);
      expect(screen.getByText('21 at your level in the last 30 days')).toBeInTheDocument();
      expect(await screen.findByText('Passed.')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();
      expect(sent).not.toHaveBeenCalled();
    });

    it('Undo inside the window puts the row back and sends nothing', async () => {
      const user = userEvent.setup();
      const { client } = await open();
      const sent = vi.spyOn(client, 'passJob');
      await user.click(passButton());
      await user.click(await screen.findByRole('button', { name: 'Undo' }));
      await waitFor(() => expect(rows()).toHaveLength(20));
      expect(rows()[0]?.getAttribute('data-job')).toBe('9000');
      expect(screen.getByText('22 at your level in the last 30 days')).toBeInTheDocument();
      expect(rows()[0]).toHaveAttribute('aria-current', 'true'); // he is back on the row
      await leavePage();
      await settle(0);
      expect(sent).not.toHaveBeenCalled();
    });

    it('Undo puts him back on the row he passed, not the one below it', async () => {
      const user = userEvent.setup();
      await open();
      press('ArrowDown');
      press('x');
      await user.click(await screen.findByRole('button', { name: 'Undo' }));
      await waitFor(() => expect(rows()).toHaveLength(20));
      expect(rows()[1]).toHaveAttribute('aria-current', 'true');
      expect(rows()[1]?.getAttribute('data-job')).toBe('9001');
    });

    it('the window closing sends exactly one pass for that posting, and the row stays gone', async () => {
      const { client } = await open();
      const sent = vi.spyOn(client, 'passJob');
      vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
      try {
        press('x'); // the window's timer starts here, on the fake clock
        await settle(7900);
        expect(sent).not.toHaveBeenCalled();
        await settle(200);
      } finally {
        vi.useRealTimers();
      }
      await waitFor(() => expect(sent).toHaveBeenCalledTimes(1));
      expect(sent.mock.calls[0]?.[0]).toBe(9000);
      expect(sent.mock.calls[0]?.[1]).toMatchObject({ requestId: expect.any(String) });
      await waitFor(() => expect(rows().some((r) => r.getAttribute('data-job') === '9000')).toBe(false));
      expect(await count('21 at your level in the last 30 days')).toBeInTheDocument();
      await leavePage();
      await settle(0);
      expect(sent).toHaveBeenCalledTimes(1);
    });

    it('leaving the page commits the pass at once', async () => {
      const user = userEvent.setup();
      const { client } = await open({ shell: true });
      const sent = vi.spyOn(client, 'passJob');
      await user.click(passButton());
      expect(sent).not.toHaveBeenCalled();
      await user.click(await screen.findByRole('link', { name: /^Today/ }));
      await waitFor(() => expect(sent).toHaveBeenCalledTimes(1));
    });

    it('closing the tab commits the pass at once', async () => {
      const user = userEvent.setup();
      const { client } = await open();
      const sent = vi.spyOn(client, 'passJob');
      await user.click(passButton());
      await leavePage();
      await waitFor(() => expect(sent).toHaveBeenCalledTimes(1));
    });

    it('puts the row back and says what happened when the pass did not go through', async () => {
      const user = userEvent.setup();
      const { client } = await open();
      vi.spyOn(client, 'passJob').mockRejectedValue(new ApiError('network', 'Cannot reach the Knock server. Nothing was saved.'));
      await user.click(passButton());
      await leavePage();
      expect(await screen.findByText('Cannot reach the Knock server. Nothing was saved.')).toBeInTheDocument();
      await waitFor(() => expect(rows()).toHaveLength(20));
      expect(rows()[0]?.getAttribute('data-job')).toBe('9000');
    });
  });

  describe('Applied', () => {
    const appliedButton = (i = 0) => within(rows()[i] as HTMLElement).getByRole('button', { name: 'Mark applied' });

    it('takes the row off at once, says so with an Undo, and sends nothing until the window closes', async () => {
      const user = userEvent.setup();
      const { client } = await open();
      const sent = vi.spyOn(client, 'markApplied');
      const passed = vi.spyOn(client, 'passJob');
      const second = titles()[1];
      await user.click(appliedButton());
      expect(rows()).toHaveLength(19);
      expect(titles()[0]).toBe(second);
      expect(screen.getByText('21 at your level in the last 30 days')).toBeInTheDocument();
      expect(await screen.findByText('Saved to Applied.')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();
      expect(sent).not.toHaveBeenCalled();
      expect(passed).not.toHaveBeenCalled();
    });

    it('Undo inside the window puts the row back and sends nothing', async () => {
      const user = userEvent.setup();
      const { client } = await open();
      const sent = vi.spyOn(client, 'markApplied');
      await user.click(appliedButton());
      await user.click(await screen.findByRole('button', { name: 'Undo' }));
      await waitFor(() => expect(rows()).toHaveLength(20));
      expect(rows()[0]?.getAttribute('data-job')).toBe('9000');
      await leavePage();
      await settle(0);
      expect(sent).not.toHaveBeenCalled();
    });

    it('I marks the row he is on applied, and leaving the page sends exactly one request, never a pass', async () => {
      const { client } = await open();
      const sent = vi.spyOn(client, 'markApplied');
      const passed = vi.spyOn(client, 'passJob');
      press('ArrowDown');
      press('i');
      expect(rows()).toHaveLength(19);
      expect(rows()[1]).toHaveAttribute('aria-current', 'true');
      await leavePage();
      await waitFor(() => expect(sent).toHaveBeenCalledTimes(1));
      expect(sent.mock.calls[0]?.[0]).toBe(9001);
      expect(passed).not.toHaveBeenCalled();
      await waitFor(() => expect(rows().some((r) => r.getAttribute('data-job') === '9001')).toBe(false));
    });

    it('the row stays gone when the list read after the commit does not answer', async () => {
      // The live list takes seconds to read and answers 503 busy when Knock is loaded. The write went through, so the
      // posting must stay off even though the list on screen was read before it.
      const user = userEvent.setup();
      const { client } = await open();
      const saved = vi.spyOn(client, 'markApplied');
      await user.click(appliedButton());
      const reads = vi.spyOn(client, 'jobs').mockRejectedValue(new ApiError('network', 'Knock is busy right now. Nothing was saved. Try again in a moment.'));
      await leavePage();
      await waitFor(() => expect(saved).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(reads).toHaveBeenCalled());
      await settle(0);
      await act(async () => {
        await new Promise((r) => setTimeout(r, 20));
      });
      expect(rows().some((r) => r.getAttribute('data-job') === '9000')).toBe(false);
      expect(screen.getByText('21 at your level in the last 30 days')).toBeInTheDocument();
    });

    it('a passed row stays gone when the list read after the commit does not answer', async () => {
      const user = userEvent.setup();
      const { client } = await open();
      const saved = vi.spyOn(client, 'passJob');
      await user.click(within(rows()[0] as HTMLElement).getByRole('button', { name: /^Pass/ }));
      const reads = vi.spyOn(client, 'jobs').mockRejectedValue(new ApiError('network', 'Knock is busy right now. Nothing was saved. Try again in a moment.'));
      await leavePage();
      await waitFor(() => expect(saved).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(reads).toHaveBeenCalled());
      await act(async () => {
        await new Promise((r) => setTimeout(r, 20));
      });
      expect(rows().some((r) => r.getAttribute('data-job') === '9000')).toBe(false);
      expect(screen.getByText('21 at your level in the last 30 days')).toBeInTheDocument();
    });

    it('a click on the row he is on moves him to the next row, not back to the top', async () => {
      const user = userEvent.setup();
      await open();
      press('ArrowDown');
      press('ArrowDown');
      const next = rows()[3]?.getAttribute('data-job');
      await user.click(appliedButton(2));
      expect(rows()[2]?.getAttribute('data-job')).toBe(next);
      expect(rows()[2]).toHaveAttribute('aria-current', 'true');
    });

    it('puts the row back and says what happened when it did not go through', async () => {
      const user = userEvent.setup();
      const { client } = await open();
      vi.spyOn(client, 'markApplied').mockRejectedValue(new ApiError('not_found', 'There is no posting 9000.'));
      await user.click(appliedButton());
      await leavePage();
      await waitFor(() => expect(rows()[0]?.getAttribute('data-job')).toBe('9000'));
    });

    it('the key hint names I for applied', async () => {
      await open();
      expect(screen.getByText('applied', { exact: false, selector: 'span' })).toHaveTextContent('I applied');
    });
  });

  describe('Reached', () => {
    const reachedButton = (row: HTMLElement) => within(row).getByRole('button', { name: /^Mark reached/ });
    const retell = () => rows().filter((r) => r.getAttribute('data-company') === 'Retell AI');

    it('Reached marks the company and every row of it shows reached', async () => {
      const user = userEvent.setup();
      const { client } = await open();
      const sent = vi.spyOn(client, 'markReached');
      expect(retell().length).toBeGreaterThan(1);
      for (const r of retell()) expect(r).not.toHaveTextContent(REACHED_TAG);
      await user.click(reachedButton(rows()[0] as HTMLElement));
      expect(await screen.findByText('Marked Retell AI reached.')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();
      // At once, on every row of the company, before anything is sent.
      for (const r of retell()) expect(r).toHaveTextContent('Reached Nov 10');
      expect(rows().find((r) => r.getAttribute('data-company') === 'Pylon')).not.toHaveTextContent(REACHED_TAG);
      expect(sent).not.toHaveBeenCalled();
      await leavePage();
      await waitFor(() => expect(sent).toHaveBeenCalledTimes(1));
      expect(sent.mock.calls[0]?.[0]).toBe(9000);
      expect(await within(rows()[0] as HTMLElement).findByText('Marked Retell AI reached.')).toBeInTheDocument();
      // The list read again says the same: the mark is the company's reached day now.
      await waitFor(() => expect(retell().every((r) => /Reached Nov 10/.test(r.textContent ?? ''))).toBe(true));
      expect(rows()).toHaveLength(20); // the postings stay where they were
    });

    it('every row of the company keeps its reached tag when the list read after the commit does not answer', async () => {
      const user = userEvent.setup();
      const { client } = await open();
      await user.click(reachedButton(rows()[0] as HTMLElement));
      const reads = vi.spyOn(client, 'jobs').mockRejectedValue(new ApiError('network', 'Knock is busy right now. Nothing was saved. Try again in a moment.'));
      await leavePage();
      expect(await within(rows()[0] as HTMLElement).findByText('Marked Retell AI reached.')).toBeInTheDocument();
      await waitFor(() => expect(reads).toHaveBeenCalled());
      await act(async () => {
        await new Promise((r) => setTimeout(r, 20));
      });
      for (const r of retell()) expect(r).toHaveTextContent('Reached Nov 10');
    });

    it('Reached can be undone in the undo window', async () => {
      const user = userEvent.setup();
      const { client } = await open();
      const sent = vi.spyOn(client, 'markReached');
      await user.click(reachedButton(rows()[0] as HTMLElement));
      await user.click(await screen.findByRole('button', { name: 'Undo' }));
      await waitFor(() => expect(reachedButton(rows()[0] as HTMLElement)).toBeEnabled());
      for (const r of retell()) expect(r).not.toHaveTextContent(REACHED_TAG);
      await leavePage();
      await settle(0);
      expect(sent).not.toHaveBeenCalled();
    });

    it('when the window closes it sends one request', async () => {
      const { client } = await open();
      const sent = vi.spyOn(client, 'markReached');
      vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
      try {
        press('a');
        await settle(8100);
      } finally {
        vi.useRealTimers();
      }
      expect(await within(rows()[0] as HTMLElement).findByText('Marked Retell AI reached.')).toBeInTheDocument();
      expect(sent).toHaveBeenCalledTimes(1);
    });

    it('a company he already contacted says Reached on its day, and Reached still works there', async () => {
      const user = userEvent.setup();
      await open();
      const row = rows().find((r) => r.getAttribute('data-company') === 'Cerebrium') as HTMLElement;
      expect(row).toHaveTextContent('Reached Nov 3');
      expect(row).not.toHaveTextContent('You contacted them');
      await user.click(reachedButton(row));
      await leavePage();
      expect(await within(row).findByText('Marked Cerebrium reached.')).toBeInTheDocument();
    });

    it('a failed Reached says so and the row keeps its buttons', async () => {
      const user = userEvent.setup();
      const { client } = await open();
      vi.spyOn(client, 'markReached').mockRejectedValue(new ApiError('network', 'Cannot reach the Knock server'));
      await user.click(reachedButton(rows()[0] as HTMLElement));
      await leavePage();
      await waitFor(() => expect(reachedButton(rows()[0] as HTMLElement)).toBeEnabled());
      for (const r of retell()) expect(r).not.toHaveTextContent(REACHED_TAG);
    });
  });

  describe('the outreach line', () => {
    const openWith = async (sentToday: number, requests: number, replied: number) => {
      const db = buildFixture('queue');
      db.shell.sending.sentToday = sentToday;
      db.progress.today = { ...db.progress.today, requests, replied };
      renderWithApp(<BrowsePage />, { route: '/browse', client: new FixtureClient(db) });
      await waitFor(() => expect(rows().length).toBeGreaterThan(0));
    };

    it("header shows today's outreach line linking to Progress", async () => {
      // Emails the sender sent today and LinkedIn requests he logged both count as sent.
      await openWith(31, 3, 2);
      const line = await screen.findByRole('link', { name: 'Today: 34 sent, 2 replies' });
      expect(line).toHaveAttribute('href', '/progress');
      expect(line.closest('header')).toContainElement(screen.getByRole('heading', { level: 1, name: 'Jobs' }));
    });

    it('says 1 reply, and 0 sent on a quiet day', async () => {
      await openWith(0, 0, 1);
      expect(await screen.findByRole('link', { name: 'Today: 0 sent, 1 reply' })).toBeInTheDocument();
    });

    it('waits for both counts and shows nothing when one did not load', async () => {
      const client = new FixtureClient(buildFixture('queue'));
      vi.spyOn(client, 'progress').mockRejectedValue(new ApiError('network', 'Cannot reach the Knock server'));
      renderWithApp(<BrowsePage />, { route: '/browse', client });
      await waitFor(() => expect(rows().length).toBeGreaterThan(0));
      expect(screen.queryByRole('link', { name: /^Today:/ })).not.toBeInTheDocument();
    });
  });

  describe('keys', () => {
    it('the arrows move the row, and the first row is the one on arrival', async () => {
      await open();
      expect(rows()[0]).toHaveAttribute('aria-current', 'true');
      press('ArrowDown');
      expect(rows()[1]).toHaveAttribute('aria-current', 'true');
      expect(rows()[0]).not.toHaveAttribute('aria-current');
      press('ArrowUp');
      expect(rows()[0]).toHaveAttribute('aria-current', 'true');
      press('ArrowUp');
      expect(rows()[0]).toHaveAttribute('aria-current', 'true');
    });

    it('Enter opens the posting in a new tab', async () => {
      await open();
      const opened = vi.spyOn(window, 'open').mockReturnValue(null);
      press('ArrowDown');
      press('Enter');
      expect(opened).toHaveBeenCalledWith('https://jobs.example/pylon/9001', '_blank', 'noopener,noreferrer');
      opened.mockRestore();
    });

    it('X passes the row he is on, and the next row becomes the current one', async () => {
      const { client } = await open();
      const sent = vi.spyOn(client, 'passJob').mockImplementation(() => new Promise(() => undefined));
      press('ArrowDown');
      const next = rows()[2]?.getAttribute('data-job');
      press('x');
      expect(rows()).toHaveLength(19);
      expect(rows()[1]).toHaveAttribute('aria-current', 'true');
      expect(rows()[1]?.getAttribute('data-job')).toBe(next);
      expect(sent).not.toHaveBeenCalled(); // inside the undo window
      await leavePage();
      await waitFor(() => expect(sent).toHaveBeenCalledTimes(1));
      expect(sent.mock.calls[0]?.[0]).toBe(9001);
    });

    it('A marks the row he is on reached, and Z takes it back', async () => {
      const { client } = await open({ shell: true });
      const sent = vi.spyOn(client, 'markReached');
      press('ArrowDown');
      press('a');
      expect(await screen.findByText('Marked Pylon reached.')).toBeInTheDocument();
      press('z');
      await waitFor(() => expect(within(rows()[1] as HTMLElement).getByRole('button', { name: /^Mark reached/ })).toBeEnabled());
      expect(rows()[1]).not.toHaveTextContent(REACHED_TAG);
      press('a');
      await screen.findByText('Marked Pylon reached.');
      await leavePage();
      await waitFor(() => expect(sent).toHaveBeenCalledTimes(1));
      expect(sent.mock.calls[0]?.[0]).toBe(9001);
      expect(await within(rows()[1] as HTMLElement).findByText('Marked Pylon reached.')).toBeInTheDocument();
    });

    it('typing in the search box never passes or marks done, and the arrows leave it for the list', async () => {
      const user = userEvent.setup();
      const { client } = await open();
      const pass = vi.spyOn(client, 'passJob');
      const ahead = vi.spyOn(client, 'markReached');
      await user.click(search());
      await user.keyboard('xa');
      expect(search()).toHaveValue('xa');
      expect(pass).not.toHaveBeenCalled();
      expect(ahead).not.toHaveBeenCalled();
      await user.clear(search());
      await waitFor(() => expect(rows().length).toBeGreaterThan(0));
      await user.keyboard('{ArrowDown}');
      expect(search()).not.toHaveFocus();
    });

    it('the keys do nothing on an empty list', async () => {
      const user = userEvent.setup();
      const { client } = await open();
      const pass = vi.spyOn(client, 'passJob');
      await user.type(search(), 'zzzz');
      await screen.findByText(/^No jobs/);
      const done = vi.spyOn(client, 'markReached');
      press('x');
      press('a');
      expect(pass).not.toHaveBeenCalled();
      expect(done).not.toHaveBeenCalled();
    });
  });
});
