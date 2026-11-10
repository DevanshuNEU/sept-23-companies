import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { queryKeys, type CheckStatus } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture } from '@/api/fixture/data';
import { renderWithApp } from '@/test/render';
import BrowsePage from './index';
import { LAST_OPENED_KEY } from './new-rows';

/*
 * Check now and new rows on the job list (#194, design 4.3 and 4.5), on the sample. A click asks for a check; the page
 * reads its status every second while it waits or runs. The sample check walks queued, running (1,240, 2,710 and 3,655
 * of 4,013 boards) and done in 3 min 10 s, and finds 6 new postings (9100 to 9105) that sort above everything else.
 */

const list = () => screen.getByRole('list', { name: 'Jobs' });
const rows = () => Array.from(list().querySelectorAll<HTMLElement>(':scope > li[data-job]'));
const firstIds = (n: number) => rows().slice(0, n).map((r) => Number(r.getAttribute('data-job')));
const row = (id: number) => document.querySelector<HTMLElement>(`li[data-job="${id}"]`) as HTMLElement;
const scroller = () => document.querySelector<HTMLElement>('[data-jobs-scroll]') as HTMLElement;
/** The page's header: the line under the title lives here (the live region repeats some of it for a screen reader). */
const head = () => within(screen.getByRole('heading', { level: 1, name: 'Jobs' }).closest('header') as HTMLElement);
const checkButton = () => screen.getByRole('button', { name: /^Check now/ });
/** One second of the status poll. */
const second = () => act(() => vi.advanceTimersByTimeAsync(1000));

async function open(setup: (c: FixtureClient) => void = () => {}) {
  const client = new FixtureClient(buildFixture('queue'));
  setup(client);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = renderWithApp(<BrowsePage />, { route: '/browse', client, queryClient });
  await screen.findByRole('heading', { level: 1, name: 'Jobs' });
  await waitFor(() => expect(rows().length).toBeGreaterThan(0));
  return { ...view, client, queryClient };
}

/** The sample check run to its end behind the page's back (as the crawler does), then the pulse's refetch of the list. */
async function findNewJobs(client: FixtureClient, queryClient: QueryClient) {
  await client.checkNow({ requestId: `test-${Math.random()}` });
  for (let i = 0; i < 5; i++) await client.checkStatus();
  await act(() => queryClient.invalidateQueries({ queryKey: queryKeys.jobsAll }));
}

function scrollDown() {
  scroller().scrollTop = 400;
  fireEvent.scroll(scroller());
}

describe('Check now', () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    act(() => {
      toast.dismiss();
    });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows progress while a check runs', async () => {
    await open();
    expect(head().queryByText(/^Checked/)).not.toBeInTheDocument();
    expect(checkButton()).toHaveAttribute('aria-keyshortcuts', 'c');
    fireEvent.keyDown(document.body, { key: 'c' });
    expect(await head().findByText('Waiting for the crawler. The check starts when it is free.')).toBeInTheDocument();
    await second();
    expect(await head().findByText('Checked 1,240 of 4,013 boards, 3 new')).toBeInTheDocument();
    await second();
    expect(await head().findByText('Checked 2,710 of 4,013 boards, 4 new')).toBeInTheDocument();
  });

  it('shows the done line with the count', async () => {
    const { client } = await open();
    const reads = vi.spyOn(client, 'checkStatus');
    const user = userEvent.setup();
    await user.click(checkButton());
    for (let i = 0; i < 4; i++) await second();
    expect(await head().findByText('Checked 4,013 boards in 3 min 10 s. 6 new.')).toBeInTheDocument();
    // Done: the poll stops.
    const seen = reads.mock.calls.length;
    await second();
    await second();
    expect(reads.mock.calls.length).toBe(seen);
    // The rows it found are on the list.
    await waitFor(() => expect(firstIds(1)).toEqual([9100]));
  });

  it('a second click while running shows the running check', async () => {
    const { client } = await open();
    const asks = vi.spyOn(client, 'checkNow');
    const user = userEvent.setup();
    await user.click(checkButton());
    await second();
    expect(await head().findByText('Checked 1,240 of 4,013 boards, 3 new')).toBeInTheDocument();
    await user.click(checkButton());
    await waitFor(() => expect(asks).toHaveBeenCalledTimes(2));
    await expect(asks.mock.results[1]?.value).resolves.toMatchObject({ state: 'running' });
    // Still the same check, going on from where it was: no new start, no second walk.
    expect(await head().findByText(/^Checked 2,710 of 4,013 boards, 4 new/)).toBeInTheDocument();
    expect(head().queryByText(/^Waiting/)).not.toBeInTheDocument();
  });

  it('shows a paused host, and a check he did not start in this visit', async () => {
    const running: CheckStatus = { state: 'running', checked: 1240, total: 4013, new: 3, startedAt: '2026-11-10T15:14:00Z', finishedAt: null, pausedHosts: ['greenhouse'] };
    await open((c) => {
      vi.spyOn(c, 'checkStatus').mockResolvedValue(running);
    });
    expect(await head().findByText('Greenhouse paused: too many requests')).toBeInTheDocument();
    expect(head().getByText('Checked 1,240 of 4,013 boards, 3 new')).toBeInTheDocument();
  });

  it('says a check asked for outside the live checkout waits for the next crawl', async () => {
    const { client, queryClient } = await open();
    vi.spyOn(client, 'checkNow').mockResolvedValue({ requestedAt: '2026-11-10T15:14:00Z', state: 'not_live' });
    await userEvent.setup().click(checkButton());
    expect(await head().findByText('Check asked for. It starts with the next crawl.')).toBeInTheDocument();
    // The next crawl picks it up: from then on the line is the check's own.
    vi.spyOn(client, 'checkStatus').mockResolvedValue({ state: 'done', checked: 4013, total: 4013, new: 2, startedAt: '2026-11-10T15:20:00Z', finishedAt: '2026-11-10T15:21:00Z', pausedHosts: [] });
    await act(() => queryClient.invalidateQueries({ queryKey: queryKeys.checkStatus }));
    expect(await head().findByText('Checked 4,013 boards in 1 min. 2 new.')).toBeInTheDocument();
    expect(head().queryByText(/^Check asked for/)).not.toBeInTheDocument();
  });

  it('a check that did not go through says so, and nothing is shown as running', async () => {
    const { client } = await open();
    const { ApiError } = await import('@/api');
    vi.spyOn(client, 'checkNow').mockRejectedValue(new ApiError('network', 'Cannot reach the Knock server'));
    await userEvent.setup().click(checkButton());
    expect(await screen.findByText('Cannot reach the Knock server')).toBeInTheDocument();
    expect(head().queryByText(/^Starting|^Waiting|^Checked/)).not.toBeInTheDocument();
  });

  it('a finished check from an earlier visit is not shown', async () => {
    const done: CheckStatus = { state: 'done', checked: 4013, total: 4013, new: 6, startedAt: '2026-11-10T09:00:00Z', finishedAt: '2026-11-10T09:03:10Z', pausedHosts: [] };
    let reads: ReturnType<typeof vi.fn> | undefined;
    await open((c) => {
      reads = vi.spyOn(c, 'checkStatus').mockResolvedValue(done) as unknown as ReturnType<typeof vi.fn>;
    });
    await waitFor(() => expect(reads).toHaveBeenCalled());
    await second();
    expect(head().queryByText(/^Checked/)).not.toBeInTheDocument();
  });
});

describe('new rows', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('holds new rows while scrolled', async () => {
    const { client, queryClient } = await open();
    const before = firstIds(3);
    scrollDown();
    await findNewJobs(client, queryClient);
    expect(await screen.findByRole('button', { name: '6 new' })).toBeInTheDocument();
    expect(firstIds(3)).toEqual(before);
    expect(row(9100)).toBeNull();
  });

  it('holds new rows while a row is open, even at the top', async () => {
    const { client, queryClient } = await open();
    fireEvent.keyDown(document.body, { key: 'ArrowDown' });
    await waitFor(() => expect(rows()[1]).toHaveAttribute('aria-current', 'true'));
    await findNewJobs(client, queryClient);
    expect(await screen.findByRole('button', { name: '6 new' })).toBeInTheDocument();
    expect(rows()[1]).toHaveAttribute('aria-current', 'true');
    expect(firstIds(2)).toEqual([9000, 9001]);
  });

  it('shows new rows at once at the top', async () => {
    const { client, queryClient } = await open();
    await findNewJobs(client, queryClient);
    await waitFor(() => expect(firstIds(7)).toEqual([9100, 9101, 9102, 9103, 9104, 9105, 9000]));
    expect(screen.queryByRole('button', { name: /new$/ })).not.toBeInTheDocument();
  });

  it('clicking N new reveals and scrolls to top', async () => {
    const { client, queryClient } = await open();
    window.localStorage.setItem(LAST_OPENED_KEY, '2026-01-01T00:00:00.000Z');
    scrollDown();
    await findNewJobs(client, queryClient);
    const opened = Date.now();
    await userEvent.setup().click(await screen.findByRole('button', { name: '6 new' }));
    await waitFor(() => expect(firstIds(1)).toEqual([9100]));
    expect(scroller().scrollTop).toBe(0);
    expect(rows()[0]).toHaveAttribute('aria-current', 'true');
    expect(screen.queryByRole('button', { name: '6 new' })).not.toBeInTheDocument();
    expect(Date.parse(window.localStorage.getItem(LAST_OPENED_KEY) ?? '')).toBeGreaterThanOrEqual(opened);
  });

  it('marks rows first seen since the last visit as new', async () => {
    window.localStorage.setItem(LAST_OPENED_KEY, '2026-11-08T00:00:00.000Z');
    const mounted = Date.now();
    await open();
    expect(within(row(9000)).getByText('New')).toBeInTheDocument(); // first seen today
    expect(within(row(9001)).queryByText('New')).not.toBeInTheDocument(); // a repost
    expect(within(row(9080)).queryByText('New')).not.toBeInTheDocument(); // found on its board's first read
    expect(within(row(9005)).queryByText('New')).not.toBeInTheDocument(); // first seen before the last visit
    // Opening the page is the new last visit; the marks stay for this one.
    expect(Date.parse(window.localStorage.getItem(LAST_OPENED_KEY) ?? '')).toBeGreaterThanOrEqual(mounted);
    expect(within(row(9000)).getByText('New')).toBeInTheDocument();
  });

  it('nothing is marked new on a first visit', async () => {
    await open();
    expect(within(list()).queryByText('New')).not.toBeInTheDocument();
  });
});
