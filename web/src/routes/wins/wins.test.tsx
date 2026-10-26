import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient } from '@tanstack/react-query';
import { useLocation } from 'react-router';
import { toast } from 'sonner';
import { queryKeys } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture, type FixtureDb, type Scenario } from '@/api/fixture/data';
import { AppShell } from '@/shell/app-shell';
import { renderWithApp } from '@/test/render';
import WinsPage from './index';

const press = (key: string) => fireEvent.keyDown(document.body, { key });

function Where() {
  const { pathname, search } = useLocation();
  return <p data-testid="where">{pathname + search}</p>;
}

type Open = { db?: FixtureDb; scenario?: Scenario; route?: string; queryClient?: QueryClient };

async function open(o: Open = {}) {
  const db = o.db ?? buildFixture(o.scenario ?? 'default');
  const client = new FixtureClient(db);
  const queryClient = o.queryClient ?? new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = renderWithApp(
    <>
      <AppShell />
      <WinsPage />
      <Where />
    </>,
    { client, queryClient, route: o.route ?? '/wins' },
  );
  return { ...view, client, db, queryClient };
}

const loaded = () => screen.findByRole('heading', { level: 1, name: 'Wins' });
/** The entries, newest first, across every month. */
const entries = () => screen.getAllByTestId('win');
const lineOf = (el: HTMLElement) => el.querySelector('[data-line]')?.textContent;
const sidebarWins = () => within(document.querySelector<HTMLElement>('[data-slot="sidebar"]') ?? document.body).getByRole('link', { name: /^Wins/ });

afterEach(() => {
  toast.dismiss();
  window.localStorage.clear();
});

describe('WinsPage', () => {
  it('lists every kept win newest first, grouped by month, with the counts line', async () => {
    await open();
    await loaded();
    await screen.findAllByTestId('win');
    expect(entries().map(lineOf)).toEqual([
      'Priya Natarajan at Browserbase replied and wants to talk.',
      'Interview at Distyl AI, set up by Camille Roche.',
      'Hugo Lindqvist at Supabase replied and wants to talk.',
      'Imani Okafor at Crusoe referred you.',
      'Noor Haddad at Graphite replied and wants to talk.',
    ]);
    expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual(['November', 'October']);
    // Zero offers are not named; every other count is, in plain words.
    expect(screen.getByTestId('counts')).toHaveTextContent('Since Oct 18: 1 interview, 1 referral, 3 replies, 0 accepted requests.');
  });

  it('writes the date, the person and the role under each line, with a link to the company', async () => {
    await open();
    await screen.findAllByTestId('win');
    const crusoe = entries()[3];
    if (!crusoe) throw new Error('fixture: a fourth win');
    const row = within(crusoe);
    expect(row.getByText('Nov 2')).toBeInTheDocument();
    expect(row.getByRole('link', { name: 'Crusoe' })).toHaveAttribute('href', expect.stringMatching(/^\/companies\/\d+$/));
    expect(crusoe).toHaveTextContent('Software Engineer');
    expect(crusoe).toHaveTextContent('For Software Engineer, New Grad.');
    expect(row.getByText('Referral')).toBeInTheDocument();
  });

  it('keeps What you sent folded until it is opened', async () => {
    await open();
    await screen.findAllByTestId('win');
    const first = entries()[0];
    if (!first) throw new Error('fixture: a win');
    const trigger = within(first).getByRole('button', { name: 'What you sent' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(within(first).queryByText(/pooling browser sessions/)).not.toBeInTheDocument();
    await userEvent.setup().click(trigger);
    expect(within(first).getByText(/pooling browser sessions/)).toBeInTheDocument();
  });

  it('offers no What you sent for a win with no note', async () => {
    const db = buildFixture();
    const win = db.wins.wins[0];
    if (!win) throw new Error('fixture: a win');
    win.sentNote = null;
    await open({ db });
    await screen.findAllByTestId('win');
    expect(within(entries()[0] as HTMLElement).queryByRole('button', { name: 'What you sent' })).not.toBeInTheDocument();
  });

  it('1 and 2 switch the toggle and the URL', async () => {
    await open();
    await screen.findAllByTestId('win');
    const all = screen.getByRole('tab', { name: /^All/ });
    const big = screen.getByRole('tab', { name: /^Referrals and interviews/ });
    expect(all).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('where')).toHaveTextContent(/^\/wins$/);

    press('2');
    await waitFor(() => expect(big).toHaveAttribute('aria-selected', 'true'));
    expect(screen.getByTestId('where')).toHaveTextContent('/wins?show=big');
    expect(entries().map(lineOf)).toEqual(['Interview at Distyl AI, set up by Camille Roche.', 'Imani Okafor at Crusoe referred you.']);
    // Months with nothing under them are gone too.
    expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual(['November']);

    press('1');
    await waitFor(() => expect(all).toHaveAttribute('aria-selected', 'true'));
    expect(screen.getByTestId('where')).toHaveTextContent(/^\/wins$/);
    expect(entries()).toHaveLength(5);
  });

  it('opens on the filter the URL names', async () => {
    await open({ route: '/wins?show=big' });
    await screen.findAllByTestId('win');
    expect(screen.getByRole('tab', { name: /^Referrals and interviews/ })).toHaveAttribute('aria-selected', 'true');
    expect(entries()).toHaveLength(2);
  });

  it('says what lands in the filter when it holds nothing', async () => {
    const db = buildFixture();
    db.wins.wins = db.wins.wins.filter((w) => w.kind === 'replied');
    await open({ db, route: '/wins?show=big' });
    expect(await screen.findByText('No referrals or interviews yet.')).toBeInTheDocument();
    expect(screen.getByText('They land here the moment you log them.')).toBeInTheDocument();
    expect(screen.getByTestId('counts')).toBeInTheDocument();
  });

  it('opening the page marks wins seen but keeps the New marks for the visit', async () => {
    const { client } = await open({ scenario: 'win' });
    await screen.findAllByTestId('win');
    const dana = entries()[0];
    if (!dana) throw new Error('fixture: a win');
    expect(lineOf(dana)).toBe('Dana Whitfield at Resend accepted your request.');
    expect(within(dana).getByText('New')).toBeInTheDocument();
    expect(within(dana).getByText('Today')).toBeInTheDocument();
    // Marked seen at once, with no toast and no undo.
    await waitFor(async () => expect((await client.wins()).unseen).toBe(0));
    expect(screen.queryByRole('button', { name: 'Undo' })).not.toBeInTheDocument();
    // A poll changes nothing on screen: the mark stays until he leaves.
    await act(() => new Promise((r) => setTimeout(r, 30)));
    expect(within(entries()[0] as HTMLElement).getByText('New')).toBeInTheDocument();
    // Only the new one carries it.
    expect(screen.getAllByText('New')).toHaveLength(1);
  });

  it('the sidebar Wins badge clears once they are seen', async () => {
    await open({ scenario: 'win' });
    await screen.findAllByTestId('win');
    await waitFor(() => expect(sidebarWins()).toHaveAccessibleName('Wins'));
  });

  it('marks a win that arrives while the page is open, and keeps its New mark', async () => {
    const { db, queryClient, client } = await open({ scenario: 'default' });
    await screen.findAllByTestId('win');
    expect(screen.queryByText('New')).not.toBeInTheDocument();
    // A reply found by the reader lands in the data; the next poll brings it in.
    const base = db.wins.wins[0];
    if (!base) throw new Error('fixture: a win');
    db.wins.wins.unshift({ ...base, winId: 5001, line: 'Maya Chen at Hex replied and wants to talk.', at: '2026-11-10T15:00:00.000Z', unseen: true });
    db.wins.unseen = 1;
    await act(() => queryClient.invalidateQueries({ queryKey: queryKeys.wins }));
    const first = await screen.findByText('Maya Chen at Hex replied and wants to talk.');
    expect(within(first.closest('li') as HTMLElement).getByText('New')).toBeInTheDocument();
    await waitFor(async () => expect((await client.wins()).unseen).toBe(0));
    expect(within(first.closest('li') as HTMLElement).getByText('New')).toBeInTheDocument();
  });

  it('a said-no reply is not in the log', async () => {
    const db = buildFixture('default');
    const client = new FixtureClient(db);
    await client.logOutcome(2202, 'replied', 1, { requestId: 'said-no' }, 'said_no');
    renderWithApp(<WinsPage />, { client, route: '/wins' });
    await screen.findAllByTestId('win');
    expect(entries()).toHaveLength(5);
    expect(screen.queryByText(/Warp/)).not.toBeInTheDocument();
  });

  it('says what lands here when there are no wins yet', async () => {
    await open({ scenario: 'fresh' });
    expect(await screen.findByText('No wins yet.')).toBeInTheDocument();
    expect(screen.getByText('A request that is accepted or a reply lands here the moment you know.')).toBeInTheDocument();
    expect(screen.queryByTestId('counts')).not.toBeInTheDocument();
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
  });

  it('has no red, no guilt word, and no percent', async () => {
    await open({ scenario: 'win' });
    await screen.findAllByTestId('win');
    const page = screen.getByTestId('wins-page');
    const tokens = [...page.querySelectorAll('*')].flatMap((e) => [...e.classList]);
    expect(tokens.filter((c) => /^(?:text|bg|border)-(?:bad|red)/.test(c))).toEqual([]);
    expect(page.textContent).not.toMatch(/overdue|behind|missed|streak|keep it up|%/i);
  });
});
