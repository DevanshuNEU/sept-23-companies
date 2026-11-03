import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient } from '@tanstack/react-query';
import { ApiError } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture } from '@/api/fixture/data';
import type { GoAheadResult } from '@/api';
import { renderWithApp } from '@/test/render';
import BrowsePage from './index';

/*
 * The job list (#156, the /browse page), on the sample: 80 postings from 10 companies, newest first. At your level: 60
 * (20 in the last 30 days, 6 in the last 7); at higher levels: 20 (6 in the last 30 days). He reads every posting
 * himself: Pass hides one, Go ahead puts its company on today's list.
 */

const press = (key: string, init: KeyboardEventInit = {}) => fireEvent.keyDown(document.body, { key, ...init });
const list = () => screen.getByRole('list', { name: 'Jobs' });
const rows = () => Array.from(list().querySelectorAll<HTMLElement>(':scope > li[data-job]'));
const titles = () => rows().map((r) => within(r).getAllByRole('link')[0]?.textContent);
const companies = () => rows().map((r) => r.getAttribute('data-company'));
const search = () => screen.getByRole('searchbox', { name: 'Search by company name' });
const count = (text: string | RegExp) => screen.findByText(text);

async function open() {
  const client = new FixtureClient(buildFixture('queue'));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = renderWithApp(<BrowsePage />, { route: '/browse', client, queryClient });
  await screen.findByRole('heading', { level: 1, name: 'Jobs' });
  await waitFor(() => expect(rows().length).toBeGreaterThan(0));
  return { ...view, client, queryClient };
}

describe('the Jobs page', () => {
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
    await waitFor(() => expect(new Set(companies())).toEqual(new Set(['Baseten', 'Helix', 'Kestrel'])));
    await user.click(within(group).getByRole('button', { name: 'Not known' }));
    await waitFor(() => expect(new Set(companies())).toEqual(new Set(['Axiom'])));
    await user.click(within(group).getByRole('button', { name: 'Small' }));
    await waitFor(() => expect(companies()).not.toContain('Axiom'));
    expect(companies()).not.toContain('Baseten');
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
    it('takes the row off at once, before the answer, and sends one pass for that posting', async () => {
      const user = userEvent.setup();
      const { client } = await open();
      const sent = vi.spyOn(client, 'passJob').mockImplementation(() => new Promise(() => undefined));
      const second = titles()[1];
      await user.click(within(rows()[0] as HTMLElement).getByRole('button', { name: /^Pass/ }));
      expect(rows()).toHaveLength(19);
      expect(titles()[0]).toBe(second);
      expect(screen.getByText('19 at your level in the last 30 days')).toBeInTheDocument();
      expect(sent).toHaveBeenCalledTimes(1);
      expect(sent.mock.calls[0]?.[0]).toBe(9000);
      expect(sent.mock.calls[0]?.[1]).toMatchObject({ requestId: expect.any(String) });
    });

    it('stays gone after the list is read again', async () => {
      const user = userEvent.setup();
      const { queryClient } = await open();
      await user.click(within(rows()[0] as HTMLElement).getByRole('button', { name: /^Pass/ }));
      await waitFor(() => expect(queryClient.isFetching()).toBe(0));
      expect(rows().some((r) => r.getAttribute('data-job') === '9000')).toBe(false);
      expect(await count('19 at your level in the last 30 days')).toBeInTheDocument();
    });

    it('puts the row back and says what happened when the pass did not go through', async () => {
      const user = userEvent.setup();
      const { client } = await open();
      vi.spyOn(client, 'passJob').mockRejectedValue(new ApiError('network', 'Cannot reach the Knock server. Nothing was saved.'));
      await user.click(within(rows()[0] as HTMLElement).getByRole('button', { name: /^Pass/ }));
      await waitFor(() => expect(rows()).toHaveLength(20));
      expect(rows()[0]?.getAttribute('data-job')).toBe('9000');
      expect(await screen.findByText('Cannot reach the Knock server. Nothing was saved.')).toBeInTheDocument();
    });
  });

  describe('Go ahead', () => {
    it('shows the answer in the row: the sentence the API gave', async () => {
      const user = userEvent.setup();
      const { client } = await open();
      const sent = vi.spyOn(client, 'goAhead');
      await user.click(within(rows()[0] as HTMLElement).getByRole('button', { name: /^Go ahead/ }));
      expect(await within(rows()[0] as HTMLElement).findByText("Added Retell AI to today's list.")).toBeInTheDocument();
      expect(sent).toHaveBeenCalledTimes(1);
      expect(sent.mock.calls[0]?.[0]).toBe(9000);
      expect(within(rows()[0] as HTMLElement).queryByRole('button', { name: /^Go ahead/ })).not.toBeInTheDocument();
      expect(rows()).toHaveLength(20); // the posting stays where it was
    });

    it('shows the sentence for a posting kept for the next list, as given', async () => {
      const user = userEvent.setup();
      const { client } = await open();
      const kept: GoAheadResult = {
        postingId: 9000, companyId: 7001, entryId: null, state: 'requested', rank: null, listDate: '2026-11-16',
        text: 'Retell AI is kept for Monday, Nov 16. It goes first on that day’s list.',
      };
      vi.spyOn(client, 'goAhead').mockResolvedValue(kept);
      await user.click(within(rows()[0] as HTMLElement).getByRole('button', { name: /^Go ahead/ }));
      expect(await within(rows()[0] as HTMLElement).findByText(kept.text)).toBeInTheDocument();
    });

    it('a refusal is said plainly and the row keeps its buttons', async () => {
      const user = userEvent.setup();
      await open();
      const row = rows().find((r) => r.getAttribute('data-company') === 'Cerebrium') as HTMLElement;
      await user.click(within(row).getByRole('button', { name: /^Go ahead/ }));
      expect(await screen.findByText('You contacted Cerebrium and are waiting for an answer. Nothing was saved.')).toBeInTheDocument();
      expect(within(row).getByRole('button', { name: /^Go ahead/ })).toBeEnabled();
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
      expect(sent.mock.calls[0]?.[0]).toBe(9001);
      expect(rows()).toHaveLength(19);
      expect(rows()[1]).toHaveAttribute('aria-current', 'true');
      expect(rows()[1]?.getAttribute('data-job')).toBe(next);
    });

    it('A goes ahead on the row he is on', async () => {
      const { client } = await open();
      const sent = vi.spyOn(client, 'goAhead');
      press('ArrowDown');
      press('a');
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
