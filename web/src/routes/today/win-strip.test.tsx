import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { queryKeys, type Win } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture, type FixtureDb, type Scenario } from '@/api/fixture/data';
import { useLocation } from 'react-router';
import { AppShell } from '@/shell/app-shell';
import { renderWithApp } from '@/test/render';
import TodayPage from './index';

const press = (key: string) => fireEvent.keyDown(document.body, { key });

/** Leaving the page commits what waits in the undo window, at once. */
const leavePage = () =>
  act(() => {
    window.dispatchEvent(new Event('pagehide'));
  });

type Open = { db?: FixtureDb; scenario?: Scenario; queryClient?: QueryClient };

async function open(o: Open = {}) {
  const db = o.db ?? buildFixture(o.scenario ?? 'win');
  const client = new FixtureClient(db);
  const queryClient = o.queryClient ?? new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = renderWithApp(
    <>
      <AppShell />
      <TodayPage />
    </>,
    { client, queryClient },
  );
  return { ...view, client, db, queryClient };
}

const NAME = 'New wins';
const strip = () => screen.getByRole('status', { name: NAME });
const maybeStrip = () => screen.queryByRole('status', { name: NAME });
const DANA = 'Dana Whitfield at Resend accepted your request.';
const sidebarWins = () => screen.getByRole('link', { name: /^Wins/ });

const realMatchMedia = window.matchMedia;
afterEach(() => {
  toast.dismiss();
  window.localStorage.clear();
  window.matchMedia = realMatchMedia;
});

/** More unseen wins than the strip names, all older than Dana's accept at 9:41. */
function crowd(db: FixtureDb, n: number) {
  const base = db.wins.wins[0];
  if (!base) throw new Error('fixture: a win');
  const extra: Win[] = Array.from({ length: n }, (_, i) => ({
    ...base,
    winId: 4000 + i,
    kind: 'replied',
    at: `2026-11-10T1${i}:00:00.000Z`,
    line: `Person ${i} at Company ${i} replied and wants to talk.`,
    replyKind: 'interested',
    unseen: true,
  }));
  db.wins.wins.unshift(...extra);
  db.wins.unseen = db.wins.wins.filter((w) => w.unseen).length;
}

function Where() {
  return <p data-testid="where">{useLocation().pathname}</p>;
}

describe('WinStrip', () => {
  it('g then w goes to the win log and does not mark anything seen', async () => {
    const client = new FixtureClient(buildFixture('win'));
    renderWithApp(
      <>
        <AppShell />
        <TodayPage />
        <Where />
      </>,
      { client },
    );
    await screen.findByRole('status', { name: NAME });
    const seen = vi.spyOn(client, 'markWinsSeen');
    press('g');
    press('w');
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/wins'));
    await leavePage();
    expect(seen).not.toHaveBeenCalled();
    expect(strip()).toBeInTheDocument();
  });

  it('shows an unseen win on top of Today, as a status, with its line, its time and the way to the win log', async () => {
    await open();
    const el = await screen.findByRole('status', { name: NAME });
    expect(within(el).getByText(DANA)).toBeInTheDocument();
    expect(within(el).getByText('9:41')).toBeInTheDocument();
    expect(within(el).getByRole('link', { name: 'Open the win log' })).toHaveAttribute('href', '/wins');
    expect(el).toHaveAttribute('aria-live', 'polite');
    // Above everything: the date heading of the plan comes after it.
    const heading = await screen.findByRole('heading', { level: 2, name: 'Tuesday, November 10' });
    expect(el.compareDocumentPosition(heading)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it('shows nothing when no win is unseen', async () => {
    await open({ scenario: 'default' });
    await screen.findByRole('heading', { level: 2, name: 'Tuesday, November 10' });
    await act(() => new Promise((r) => setTimeout(r, 30)));
    expect(maybeStrip()).not.toBeInTheDocument();
  });

  it('an unseen win survives a poll and a reload', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { db, unmount } = await open({ queryClient });
    await screen.findByRole('status', { name: NAME });
    // Two polls: the same answer, the same strip.
    await act(() => queryClient.invalidateQueries({ queryKey: queryKeys.wins }));
    await act(() => queryClient.invalidateQueries({ queryKey: queryKeys.wins }));
    expect(within(strip()).getByText(DANA)).toBeInTheDocument();
    // A reload: a new page over the same stored data.
    unmount();
    await open({ db });
    expect(within(await screen.findByRole('status', { name: NAME })).getByText(DANA)).toBeInTheDocument();
  });

  it('w marks seen, the strip goes, and undo within the window brings it back', async () => {
    const { client } = await open();
    await screen.findByRole('status', { name: NAME });
    const seen = vi.spyOn(client, 'markWinsSeen');
    expect(sidebarWins()).toHaveAccessibleName('Wins, 1 new');
    press('w');
    expect(maybeStrip()).not.toBeInTheDocument();
    expect(await screen.findByText('Marked seen.')).toBeInTheDocument();
    expect(sidebarWins()).toHaveAccessibleName('Wins');
    press('z');
    await waitFor(() => expect(maybeStrip()).toBeInTheDocument());
    expect(sidebarWins()).toHaveAccessibleName('Wins, 1 new');
    await leavePage();
    expect(seen).not.toHaveBeenCalled();
  });

  it('the Mark seen button does the same as w, and a seen win stays seen once the window ends', async () => {
    const { client } = await open();
    await screen.findByRole('status', { name: NAME });
    const seen = vi.spyOn(client, 'markWinsSeen');
    const button = within(strip()).getByRole('button', { name: 'Mark seen' });
    expect(button).toHaveAttribute('aria-keyshortcuts', 'w');
    fireEvent.click(button);
    expect(maybeStrip()).not.toBeInTheDocument();
    await leavePage();
    await waitFor(() => expect(seen).toHaveBeenCalledTimes(1));
    expect(seen.mock.calls[0]?.[0]).toEqual([3010]);
    await waitFor(async () => expect((await client.wins()).unseen).toBe(0));
    expect(maybeStrip()).not.toBeInTheDocument();
  });

  it('a said-no reply is not a win', async () => {
    const db = buildFixture('default');
    const client = new FixtureClient(db);
    await client.logOutcome(2202, 'replied', 1, { requestId: 'said-no' }, 'said_no');
    renderWithApp(
      <>
        <AppShell />
        <TodayPage />
      </>,
      { client },
    );
    await screen.findByRole('region', { name: /Last week, what happened/ });
    await act(() => new Promise((r) => setTimeout(r, 30)));
    expect(maybeStrip()).not.toBeInTheDocument();
    expect((await client.wins()).wins.some((w) => w.company.name === 'Warp')).toBe(false);
  });

  it('undoing an accept inside the window removes its win with it', async () => {
    const { client } = await open({ scenario: 'default' });
    await screen.findByRole('region', { name: /Last week, what happened/ });
    const log = vi.spyOn(client, 'logOutcome');
    press('1');
    press('z');
    await leavePage();
    await act(() => new Promise((r) => setTimeout(r, 30)));
    expect(log).not.toHaveBeenCalled();
    expect(maybeStrip()).not.toBeInTheDocument();
  });

  it('an accept that is kept arrives as a win once it is committed, and plays the entrance once', async () => {
    await open({ scenario: 'default' });
    await screen.findByRole('region', { name: /Last week, what happened/ });
    expect(maybeStrip()).not.toBeInTheDocument();
    press('1');
    await leavePage();
    const el = await screen.findByRole('status', { name: NAME });
    expect(within(el).getByText('Owen Brandt at Warp accepted your request.')).toBeInTheDocument();
    expect(el.className).toMatch(/\bwin-in\b/);
  });

  it('does not replay the entrance for a win that was already there on load', async () => {
    await open();
    const el = await screen.findByRole('status', { name: NAME });
    expect(el.className).not.toMatch(/\bwin-in\b/);
  });

  it('does not animate when reduced motion is set', async () => {
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: query.includes('prefers-reduced-motion'),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(() => false),
    }));
    await open({ scenario: 'default' });
    await screen.findByRole('region', { name: /Last week, what happened/ });
    press('1');
    await leavePage();
    const el = await screen.findByRole('status', { name: NAME });
    expect(within(el).getByText('Owen Brandt at Warp accepted your request.')).toBeInTheDocument();
    expect(el.className).not.toMatch(/\bwin-in\b/);
  });

  it('shows up to 3 lines, newest first, then "and 2 more"', async () => {
    const db = buildFixture('win');
    crowd(db, 4);
    await open({ db });
    const el = await screen.findByRole('status', { name: NAME });
    const lines = within(el).getAllByRole('listitem');
    expect(lines).toHaveLength(3);
    expect(lines[0]).toHaveTextContent(DANA);
    expect(lines[1]).toHaveTextContent('Person 3 at Company 3 replied and wants to talk.');
    expect(lines[2]).toHaveTextContent('Person 2 at Company 2');
    expect(within(el).getByText('and 2 more')).toBeInTheDocument();
  });

  it('names no "and more" line for 3 wins or fewer', async () => {
    const db = buildFixture('win');
    crowd(db, 2);
    await open({ db });
    const el = await screen.findByRole('status', { name: NAME });
    expect(within(el).getAllByRole('listitem')).toHaveLength(3);
    expect(within(el).queryByText(/and \d+ more/)).not.toBeInTheDocument();
  });

  it('writes a win from an earlier day with its date, not only a time', async () => {
    const db = buildFixture('win');
    const win = db.wins.wins[0];
    if (!win) throw new Error('fixture: a win');
    win.at = '2026-11-09T14:05:00.000Z';
    await open({ db });
    const el = await screen.findByRole('status', { name: NAME });
    expect(within(el).getByText('Nov 9, 9:05')).toBeInTheDocument();
  });

  it('has no red, no guilt word and no counter in it', async () => {
    await open();
    const el = await screen.findByRole('status', { name: NAME });
    const tokens = [...el.querySelectorAll('*')].flatMap((e) => [...e.classList]);
    expect(tokens.filter((c) => /^(?:text|bg|border)-(?:bad|red)/.test(c))).toEqual([]);
    expect(el.textContent).not.toMatch(/overdue|behind|missed|streak|keep it up/i);
    expect(el.querySelector('[data-slot="badge"]')).toBeNull();
  });
});
