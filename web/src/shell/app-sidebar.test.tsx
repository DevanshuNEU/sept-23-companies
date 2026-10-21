import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { useLocation, useNavigate } from 'react-router';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture } from '@/api/fixture/data';
import { usePendingWrite } from '@/pending/pending';
import { AppShell } from '@/shell/app-shell';
import { renderWithApp } from '@/test/render';

function Where() {
  const location = useLocation();
  return <p data-testid="where">{location.pathname + location.search}</p>;
}

/** Queues one undoable write and moves around, so the tests can watch flushes and undo. */
function Writer({ commit }: { commit: () => Promise<unknown> }) {
  const write = usePendingWrite();
  const navigate = useNavigate();
  return (
    <>
      <button
        type="button"
        onClick={() => write({ id: 'approve:1', label: 'Approved Omar Haddad', afterCommit: 'Approved.', patches: [], commit })}
      >
        queue
      </button>
      <button type="button" onClick={() => navigate('/outbox?sel=12', { replace: true })}>
        select 12
      </button>
      <textarea aria-label="Body" />
    </>
  );
}

const press = (key: string, init: KeyboardEventInit = {}, target: Element = document.body) =>
  fireEvent.keyDown(target, { key, ...init });
const where = () => screen.getByTestId('where').textContent;
// The breadcrumb's current page also has the link role, so sidebar lookups stay in the sidebar.
const sidebar = () => document.querySelector<HTMLElement>('[data-slot="sidebar"]') ?? document.body;
const nav = (name: RegExp) => within(sidebar()).getByRole('link', { name });
const ready = () => screen.findByRole('button', { name: /^Boards/ });

async function shell(route = '/today', client = new FixtureClient()) {
  const view = renderWithApp(
    <>
      <AppShell />
      <Where />
    </>,
    { route, client },
  );
  await ready();
  return view;
}

beforeEach(() => {
  window.localStorage.clear();
});

describe('AppShell sidebar', () => {
  it('shows the counts by each view', async () => {
    await shell();
    expect(within(nav(/^Today/)).getByText('2 of 7')).toBeInTheDocument();

    const outbox = nav(/^Outbox/);
    const replies = within(outbox).getByText('1');
    expect(replies.closest('[data-slot="badge"]')).toHaveAttribute('data-variant', 'ok');
    expect(within(outbox).getByText('7')).toBeInTheDocument();
    expect(within(outbox).getByText('7').closest('[data-slot="badge"]')).toBeNull();

    expect(within(nav(/^LinkedIn/)).getByText('12')).toBeInTheDocument();
    const review = within(nav(/^Needs you/)).getByText('5');
    expect(review.closest('[data-slot="badge"]')).toHaveAttribute('data-variant', 'warn');
    expect(within(nav(/^No-go/)).getByText('31')).toBeInTheDocument();
    expect(within(nav(/^Done/)).getByText('4')).toBeInTheDocument();
  });

  it('names what each count means for screen readers', async () => {
    await shell();
    expect(nav(/^Outbox/)).toHaveAccessibleName('Outbox, 1 reply to handle, 7 drafts to review');
    expect(nav(/^Needs you/)).toHaveAccessibleName('Needs you, 5 open');
  });

  it('counts people done of today\'s plan, and says so in the link name', async () => {
    await shell();
    expect(nav(/^Today/)).toHaveAccessibleName('Today, 2 of 7 done');
  });

  it('shows no count by Today when nothing is planned', async () => {
    await shell('/today', new FixtureClient(buildFixture('weekend')));
    expect(nav(/^Today/)).toHaveAccessibleName('Today');
    expect(within(nav(/^Today/)).queryByText(/ of /)).toBeNull();
  });

  it('puts Progress and Wins under Results, with no badge while no win is new', async () => {
    await shell();
    const results = screen.getByRole('navigation', { name: 'Results' });
    expect(within(results).getByRole('link', { name: 'Progress' })).toHaveAttribute('href', '/progress');
    const wins = within(results).getByRole('link', { name: 'Wins' });
    expect(wins).toHaveAttribute('href', '/wins');
    expect(wins.querySelector('[data-slot="badge"]')).toBeNull();
  });

  it('shows a green count with a dot by Wins while a win is new', async () => {
    await shell('/today', new FixtureClient(buildFixture('win')));
    const wins = nav(/^Wins/);
    expect(wins).toHaveAccessibleName('Wins, 1 new');
    const badge = within(wins).getByText('1').closest('[data-slot="badge"]');
    expect(badge).toHaveAttribute('data-variant', 'ok');
    expect(badge?.querySelector('.bg-ok-dot')).not.toBeNull();
  });

  it('marks Progress and Wins as the current view on their routes', async () => {
    await shell('/progress');
    expect(nav(/^Progress/)).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('navigation', { name: 'breadcrumb' })).toHaveTextContent('Progress');
  });

  it('titles the Wins view in the breadcrumb', async () => {
    await shell('/wins');
    expect(nav(/^Wins/)).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('navigation', { name: 'breadcrumb' })).toHaveTextContent('Wins');
  });

  it('shows the sender, replies this month and the last board read in the footer', async () => {
    await shell();
    expect(screen.getByText('Sending')).toBeInTheDocument();
    expect(screen.getByText('20 of 30 today')).toBeInTheDocument();
    expect(screen.getByText('9 replies')).toBeInTheDocument();
    expect(screen.getByText('in November')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Boards read Mon 08:04' })).toBeInTheDocument();
  });

  it('names a hard block in red in the footer', async () => {
    await shell('/today', new FixtureClient(buildFixture('hard_block')));
    expect(screen.getByText('Paused: hard block')).toBeInTheDocument();
    expect(screen.getByTestId('sender-dot')).toHaveClass('bg-bad-dot');
  });

  it('marks the current view with aria-current', async () => {
    await shell('/outbox/12');
    expect(nav(/^Outbox/)).toHaveAttribute('aria-current', 'page');
    expect(nav(/^Today/)).not.toHaveAttribute('aria-current');
  });

  it('g then o goes to the Outbox, and every g chord goes to its view', async () => {
    await shell();
    expect(nav(/^Today/)).toHaveAttribute('aria-current', 'page');

    press('g');
    press('o');
    expect(where()).toBe('/outbox');
    expect(nav(/^Outbox/)).toHaveAttribute('aria-current', 'page');

    const chords: [string, string][] = [
      ['t', '/today'],
      ['l', '/linkedin'],
      ['n', '/needs-you'],
      ['x', '/no-go'],
      ['d', '/done'],
      ['p', '/progress'],
      ['w', '/wins'],
    ];
    for (const [key, path] of chords) {
      press('g');
      press(key);
      expect(where()).toBe(path);
    }
  });

  it('escape goes back, after leaving a text field first', async () => {
    renderWithApp(
      <>
        <AppShell />
        <Where />
        <Writer commit={vi.fn(() => Promise.resolve())} />
      </>,
      { route: '/today' },
    );
    await ready();
    press('g');
    press('o');
    expect(where()).toBe('/outbox');

    const body = screen.getByRole('textbox', { name: 'Body' });
    body.focus();
    press('Escape', {}, body);
    expect(body).not.toHaveFocus();
    expect(where()).toBe('/outbox');

    press('Escape');
    expect(where()).toBe('/today');
  });

  it('mod+b collapses the sidebar once', async () => {
    const { container } = await shell();
    const sidebar = container.querySelector('[data-slot="sidebar"]');
    expect(sidebar).toHaveAttribute('data-state', 'expanded');
    press('b', { ctrlKey: true });
    expect(sidebar).toHaveAttribute('data-state', 'collapsed');
    press('b', { ctrlKey: true });
    expect(sidebar).toHaveAttribute('data-state', 'expanded');
  });

  it('opens the job runs from "Boards read Mon 08:04", with a late run in amber', async () => {
    await shell();
    fireEvent.click(screen.getByRole('button', { name: 'Boards read Mon 08:04' }));
    const pop = await screen.findByRole('dialog');

    const board = within(pop).getByRole('listitem', { name: 'Board read' });
    expect(within(board).getByText('Mon Nov 9, 08:04')).toBeInTheDocument();
    expect(within(board).getByText('Finished')).toBeInTheDocument();
    expect(within(board).getByText('Boards read 871, Empty 1, Errors 1')).toBeInTheDocument();
    expect(within(board).queryByText('Late')).toBeNull();

    for (const name of ['Daily list', 'Sender', 'Import']) {
      expect(within(pop).getByRole('listitem', { name })).toBeInTheDocument();
    }

    const rebuild = within(pop).getByRole('listitem', { name: 'Rebuild' });
    expect(within(rebuild).getByText('Late').closest('[data-slot="badge"]')).toHaveAttribute('data-variant', 'warn');
    expect(within(rebuild).getByText('Expected Sun Nov 8, 07:00')).toHaveClass('text-warn');
    expect(within(rebuild).getByText('Postings classified 3,190')).toBeInTheDocument();
  });

  it('a late rebuild drives no banner in the shell', async () => {
    await shell();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('shows a failed job with its error', async () => {
    await shell('/today', new FixtureClient(buildFixture('job_failed')));
    fireEvent.click(screen.getByRole('button', { name: 'Boards read Mon 08:04' }));
    const daily = within(await screen.findByRole('dialog')).getByRole('listitem', { name: 'Daily list' });
    expect(within(daily).getByText('Failed').closest('[data-slot="badge"]')).toHaveAttribute('data-variant', 'bad');
    expect(within(daily).getByText('Could not read the company store: database is locked')).toBeInTheDocument();
  });

  it('lists the last company pages opened under Recent', async () => {
    const client = new FixtureClient();
    const index = await client.searchIndex();
    const [first, second, third, fourth] = index.companies;
    if (!first || !second || !third || !fourth) throw new Error('the fixture has companies');
    window.localStorage.setItem('outreach.recent', JSON.stringify([second.id, first.id, third.id, fourth.id]));
    await shell(`/companies/${first.id}`, client);

    // The page just opened goes first, without a duplicate, and only three stay.
    expect(JSON.parse(window.localStorage.getItem('outreach.recent') ?? '[]')).toEqual([first.id, second.id, third.id]);
    const recent = screen.getByRole('navigation', { name: 'Recent companies' });
    const links = await within(recent).findAllByRole('link');
    expect(links.map((a) => a.textContent)).toEqual([first.name, second.name, third.name]);
    expect(links[0]).toHaveAttribute('aria-current', 'page');
    expect(links[1]).toHaveAttribute('href', `/companies/${second.id}`);
    expect(screen.getByRole('navigation', { name: 'breadcrumb' })).toHaveTextContent(`Companies${first.name}`);
  });
});

describe('AppShell top bar', () => {
  it('shows the view in the breadcrumb, the freshness and the sample badge', async () => {
    await shell('/no-go/people');
    expect(screen.getByRole('navigation', { name: 'breadcrumb' })).toHaveTextContent('No-go list');
    expect(screen.getByText('Updated 10:14')).toBeInTheDocument();
    expect(screen.getByText('Sample data. Nothing is saved.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Keyboard shortcuts' })).toBeInTheDocument();
  });

  it('hides the sample badge for a live client', async () => {
    const client = new FixtureClient();
    Object.defineProperty(client, 'source', { value: 'live' });
    await shell('/today', client);
    expect(screen.queryByText('Sample data. Nothing is saved.')).toBeNull();
  });
});

describe('AppShell keys and pending writes', () => {
  async function withWriter(route = '/today') {
    const commit = vi.fn(() => Promise.resolve());
    renderWithApp(
      <>
        <AppShell />
        <Where />
        <Writer commit={commit} />
      </>,
      { route },
    );
    await ready();
    fireEvent.click(screen.getByRole('button', { name: 'queue' }));
    await screen.findByText('Approved Omar Haddad');
    return commit;
  }

  it('z typed in a textarea does not undo', async () => {
    const commit = await withWriter();
    const body = screen.getByRole('textbox', { name: 'Body' });
    body.focus();
    press('z', {}, body);
    press('g');
    press('o');
    await waitFor(() => expect(commit).toHaveBeenCalledTimes(1));
  });

  it('z outside a field undoes the last write', async () => {
    const commit = await withWriter();
    press('z');
    press('g');
    press('o');
    expect(where()).toBe('/outbox');
    await act(() => Promise.resolve());
    expect(commit).not.toHaveBeenCalled();
  });

  it('changing ?sel= does not flush, changing view does', async () => {
    const commit = await withWriter('/outbox');
    fireEvent.click(screen.getByRole('button', { name: 'select 12' }));
    expect(where()).toBe('/outbox?sel=12');
    await act(() => Promise.resolve());
    expect(commit).not.toHaveBeenCalled();

    press('g');
    press('l');
    expect(where()).toBe('/linkedin');
    await waitFor(() => expect(commit).toHaveBeenCalledTimes(1));
    expect((await screen.findAllByText('Approved.')).length).toBeGreaterThan(0);
  });
});
