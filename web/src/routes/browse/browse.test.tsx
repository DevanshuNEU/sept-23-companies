import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient } from '@tanstack/react-query';
import { ApiError } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture } from '@/api/fixture/data';
import type { GoAheadResult } from '@/api';
import { toast } from 'sonner';
import { renderWithApp } from '@/test/render';
import { AppShell } from '@/shell/app-shell';
import BrowsePage from './index';

/*
 * The job list (#156, the /browse page), on the sample: 80 postings from 11 companies, newest first. At your level: 60
 * (20 in the last 30 days, 6 in the last 7); at higher levels: 20 (6 in the last 30 days). He reads every posting
 * himself: Pass hides one, Go ahead puts its company on today's list.
 */

const press = (key: string, init: KeyboardEventInit = {}) => fireEvent.keyDown(document.body, { key, ...init });
const list = () => screen.getByRole('list', { name: 'Jobs' });
const rows = () => Array.from(list().querySelectorAll<HTMLElement>(':scope > li[data-job]'));
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

describe('the Jobs page', () => {
  // Toasts live in a global store: one test's toast must not be another's Undo.
  beforeEach(() => {
    act(() => {
      toast.dismiss();
    });
  });

  it('says how many at your level in the last 30 days, and each row says what the posting is', async () => {
    await open();
    expect(await count('20 at your level in the last 30 days')).toBeInTheDocument();
    expect(rows()).toHaveLength(20);
    const first = rows()[0] as HTMLElement;
    const link = within(first).getByRole('link', { name: 'Software Engineer, New Grad' });
    expect(link).toHaveAttribute('href', 'https://jobs.example/retellai/9000');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'));
    expect(first).toHaveTextContent('new grad');
    expect(first).toHaveTextContent('Retell AI');
    expect(first).toHaveTextContent('posted today');
    expect(first).toHaveTextContent('45 people');
    expect(first).toHaveTextContent('San Francisco, CA');
    expect(first).toHaveTextContent('E-Verify');
    expect(first).toHaveTextContent('YC');
    expect(within(first).getByRole('button', { name: /^Pass/ })).toBeInTheDocument();
    expect(within(first).getByRole('button', { name: /^Go ahead/ })).toBeInTheDocument();
  });

  it('lists newest first, dates a posting by when it was seen when it has no date, and has no score or ranking text', async () => {
    await open();
    const ages = rows().map((r) => /(posted|seen) (today|\d+ days? ago)/.exec(r.textContent ?? '')?.[0]);
    expect(ages[0]).toBe('posted today');
    expect(ages[1]).toBe('posted 1 day ago');
    expect(ages).toContain('seen 4 days ago'); // the fifth posting has no date of its own
    expect(document.body).not.toHaveTextContent(/\bfit\b|why it ranked|score/i);
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
    expect(of('Cerebrium')).toHaveTextContent('You contacted them on Tue Nov 3');
    expect(of('Cerebrium')).toHaveTextContent('18 people');
  });

  it('the two tabs ask for the other level and say how many each holds', async () => {
    const user = userEvent.setup();
    await open();
    const yours = screen.getByRole('tab', { name: /^Your level/ });
    const higher = screen.getByRole('tab', { name: /^Higher levels/ });
    expect(yours).toHaveAttribute('aria-selected', 'true');
    expect(yours).toHaveTextContent('20');
    expect(higher).toHaveTextContent('6');
    await user.click(higher);
    expect(await count('6 at higher levels in the last 30 days')).toBeInTheDocument();
    expect(rows()).toHaveLength(6);
    expect(titles()).toContain('Senior Software Engineer');
    expect(titles()).not.toContain('Software Engineer, New Grad');
    expect(higher).toHaveAttribute('aria-selected', 'true');
  });

  it('a window chip changes what is asked, and Any time pages 50 at a time', async () => {
    const user = userEvent.setup();
    await open();
    const group = screen.getByRole('group', { name: 'Posted within' });
    expect(within(group).getByRole('button', { name: '30 days' })).toHaveAttribute('aria-pressed', 'true');
    await user.click(within(group).getByRole('button', { name: '7 days' }));
    expect(await count('6 at your level in the last 7 days')).toBeInTheDocument();
    expect(rows()).toHaveLength(6);
    await user.click(within(group).getByRole('button', { name: 'Any time' }));
    expect(await count('60 at your level')).toBeInTheDocument();
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
      expect(screen.getByText('19 at your level in the last 30 days')).toBeInTheDocument();
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
      expect(screen.getByText('20 at your level in the last 30 days')).toBeInTheDocument();
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
      expect(await count('19 at your level in the last 30 days')).toBeInTheDocument();
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

  describe('Go ahead', () => {
    const goButton = (row: HTMLElement) => within(row).getByRole('button', { name: /^Go ahead/ });

    it('says it is going ahead with an Undo, and sends nothing until the window closes', async () => {
      const user = userEvent.setup();
      const { client } = await open();
      const sent = vi.spyOn(client, 'goAhead');
      await user.click(goButton(rows()[0] as HTMLElement));
      expect(await screen.findByText('Going ahead with Retell AI.')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();
      expect(within(rows()[0] as HTMLElement).queryByRole('button', { name: /^Go ahead/ })).not.toBeInTheDocument();
      expect(sent).not.toHaveBeenCalled();
    });

    it('Undo inside the window gives the row its buttons back and sends nothing', async () => {
      const user = userEvent.setup();
      const { client } = await open();
      const sent = vi.spyOn(client, 'goAhead');
      await user.click(goButton(rows()[0] as HTMLElement));
      await user.click(await screen.findByRole('button', { name: 'Undo' }));
      await waitFor(() => expect(goButton(rows()[0] as HTMLElement)).toBeEnabled());
      await leavePage();
      await settle(0);
      expect(sent).not.toHaveBeenCalled();
    });

    it('when the window closes it sends one request and shows the sentence the API gave', async () => {
      const { client } = await open();
      const sent = vi.spyOn(client, 'goAhead');
      vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
      try {
        press('a');
        await settle(8100);
      } finally {
        vi.useRealTimers();
      }
      expect(await within(rows()[0] as HTMLElement).findByText("Added Retell AI to today's list.")).toBeInTheDocument();
      expect(sent).toHaveBeenCalledTimes(1);
      expect(sent.mock.calls[0]?.[0]).toBe(9000);
      expect(rows()).toHaveLength(20); // the posting stays where it was
    });

    it('leaving the page commits it at once, with the sentence for a posting kept for the next list', async () => {
      const user = userEvent.setup();
      const { client } = await open();
      const kept: GoAheadResult = {
        postingId: 9000, companyId: 7001, entryId: null, state: 'requested', rank: null, listDate: '2026-11-16',
        text: 'Retell AI is kept for Monday, Nov 16. It goes first on that day\u2019s list.',
      };
      vi.spyOn(client, 'goAhead').mockResolvedValue(kept);
      await user.click(goButton(rows()[0] as HTMLElement));
      await leavePage();
      expect(await within(rows()[0] as HTMLElement).findByText(kept.text)).toBeInTheDocument();
    });

    it('a refusal is said plainly and the row keeps its buttons', async () => {
      const user = userEvent.setup();
      await open();
      const row = rows().find((r) => r.getAttribute('data-company') === 'Cerebrium') as HTMLElement;
      await user.click(goButton(row));
      await leavePage();
      expect(await screen.findByText('You contacted Cerebrium and are waiting for an answer. Nothing was saved.')).toBeInTheDocument();
      await waitFor(() => expect(goButton(row)).toBeEnabled());
      expect(within(row).getByRole('button', { name: /^Pass/ })).toBeEnabled();
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

    it('A goes ahead on the row he is on, and Z takes it back', async () => {
      const { client } = await open({ shell: true });
      const sent = vi.spyOn(client, 'goAhead');
      press('ArrowDown');
      press('a');
      expect(await screen.findByText('Going ahead with Pylon.')).toBeInTheDocument();
      press('z');
      await waitFor(() => expect(within(rows()[1] as HTMLElement).getByRole('button', { name: /^Go ahead/ })).toBeEnabled());
      press('a');
      await screen.findByText('Going ahead with Pylon.');
      await leavePage();
      await waitFor(() => expect(sent).toHaveBeenCalledTimes(1));
      expect(sent.mock.calls[0]?.[0]).toBe(9001);
      expect(await within(rows()[1] as HTMLElement).findByText("Added Pylon to today's list.")).toBeInTheDocument();
    });

    it('typing in the search box never passes or goes ahead, and the arrows leave it for the list', async () => {
      const user = userEvent.setup();
      const { client } = await open();
      const pass = vi.spyOn(client, 'passJob');
      const ahead = vi.spyOn(client, 'goAhead');
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
      press('x');
      press('a');
      expect(pass).not.toHaveBeenCalled();
    });
  });
});
