import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router';
import type { SearchIndex } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture } from '@/api/fixture/data';
import { useHotkeys } from '@/keyboard/hotkeys';
import { AppShell } from '@/shell/app-shell';
import { rankSearch } from '@/shell/command-palette';
import { renderWithApp } from '@/test/render';

function Where() {
  const location = useLocation();
  return <p data-testid="where">{location.pathname + location.search}</p>;
}

/** A view that owns the single key `a` in the group "Outbox". */
function View({ run }: { run: () => void }) {
  useHotkeys([{ keys: 'a', label: 'Approve this email', group: 'Outbox', run }]);
  return null;
}

const where = () => screen.getByTestId('where').textContent;
const ready = () => screen.findByRole('button', { name: /^Boards/ });
const press = (key: string, init: KeyboardEventInit = {}) => fireEvent.keyDown(document.body, { key, ...init });

async function shell(opts: { route?: string; client?: FixtureClient; run?: () => void } = {}) {
  const view = renderWithApp(
    <>
      <AppShell />
      <Where />
      <View run={opts.run ?? (() => {})} />
    </>,
    { route: opts.route ?? '/today', client: opts.client ?? new FixtureClient() },
  );
  await ready();
  return view;
}

const palette = () => screen.findByRole('dialog', { name: 'Search and commands' });
const search = () => screen.getByRole('combobox');

let index: SearchIndex;
beforeAll(async () => {
  index = await new FixtureClient().searchIndex();
});

beforeEach(() => {
  window.localStorage.clear();
});

describe('rankSearch', () => {
  it('puts a prefix on the name first', () => {
    const names = rankSearch(index, 'br', []).companies.map((c) => c.name);
    expect(names[0]).toBe('Browserbase');
    expect(names.length).toBeGreaterThan(1);
  });

  it('puts companies in progress before recent ones, and recent ones before the rest', () => {
    const rest = index.companies.filter((c) => !c.inProgress);
    const [a, b, c] = rest;
    if (!a || !b || !c) throw new Error('the fixture has companies that are not in progress');
    const ids = rankSearch(index, '', [c.id, b.id]).companies.map((x) => x.id);
    const progress = index.companies.filter((x) => x.inProgress).length;
    expect(progress).toBeGreaterThan(0);
    expect(ids.slice(0, progress).every((id) => index.companies.find((x) => x.id === id)?.inProgress)).toBe(true);
    expect(ids.slice(progress, progress + 2)).toEqual([c.id, b.id]);
  });

  it('puts a prefix ahead of a company in progress', () => {
    const entries = index.companies.map((x) => ({ ...x, inProgress: x.name === 'Cerebrium' }));
    const names = rankSearch({ ...index, companies: entries }, 'br', []).companies.map((x) => x.name);
    const prefixed = names.filter((n) => n.toLowerCase().startsWith('br'));
    expect(names[0]).toBe('Browserbase');
    expect(names.indexOf('Cerebrium')).toBe(prefixed.length);
  });

  it('matches aliases, domains, people names, emails and titles', () => {
    const company = index.companies.find((c) => c.domain !== null);
    const person = index.people.find((p) => p.email !== null);
    if (!company?.domain || !person?.email) throw new Error('the fixture has a domain and an email');
    expect(rankSearch(index, company.domain, []).companies.map((c) => c.id)).toContain(company.id);
    expect(rankSearch(index, person.email, []).people.map((p) => p.id)).toContain(person.id);
    expect(rankSearch(index, person.name.toUpperCase(), []).people.map((p) => p.id)).toContain(person.id);
    expect(rankSearch(index, person.title, []).people.map((p) => p.id)).toContain(person.id);
    const aliased: SearchIndex = { ...index, companies: [{ ...company, aliases: ['Fancy Labs'] }] };
    expect(rankSearch(aliased, 'fancy', []).companies).toHaveLength(1);
  });

  it('reads the old dashboard synonyms', () => {
    const people: SearchIndex['people'] = [
      { id: 1, companyId: 1, companyName: 'Cerebrium', name: 'Ada Example', title: 'Member of Technical Staff', email: null, statusLine: '' },
      { id: 2, companyId: 1, companyName: 'Cerebrium', name: 'Bo Example', title: 'Forward Deployed Engineer', email: null, statusLine: '' },
    ];
    const small: SearchIndex = { asOf: index.asOf, companies: [], people };
    expect(rankSearch(small, 'mts', []).people.map((p) => p.id)).toEqual([1]);
    expect(rankSearch(small, 'FDE', []).people.map((p) => p.id)).toEqual([2]);
  });

  it('finds nothing for a query that matches nothing', () => {
    expect(rankSearch(index, 'zzzzqqqq', [])).toEqual({ companies: [], people: [] });
  });
});

describe('CommandPalette', () => {
  it('opens with mod+k and with /, and shows the four groups', async () => {
    await shell();
    press('k', { ctrlKey: true });
    const dialog = await palette();
    expect(search()).toHaveFocus();
    for (const name of ['Companies', 'People', 'Go to', 'Actions']) {
      expect(within(dialog).getByText(name)).toBeInTheDocument();
    }
    fireEvent.keyDown(search(), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    press('/');
    await palette();
  });

  it('mod+k closes it again', async () => {
    await shell();
    press('k', { ctrlKey: true });
    await palette();
    fireEvent.keyDown(search(), { key: 'k', ctrlKey: true });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('shows each result with its status in muted text', async () => {
    const user = userEvent.setup();
    await shell();
    press('/');
    await palette();
    await user.type(search(), 'browserbase');
    const row = await screen.findByRole('option', { name: /^Browserbase/ });
    expect(within(row).getByText('Held: reply from Priya Natarajan')).toHaveClass('text-muted-foreground');
  });

  it("mts shows Cerebrium's posting after 120 ms", async () => {
    const user = userEvent.setup();
    const client = new FixtureClient();
    const calls: number[] = [];
    const real = client.searchPostings.bind(client);
    vi.spyOn(client, 'searchPostings').mockImplementation((q) => {
      calls.push(performance.now());
      return real(q);
    });
    await shell({ client });
    press('/');
    await palette();
    await user.type(search(), 'mts');
    const typed = performance.now();
    expect(calls).toHaveLength(0);
    await act(() => new Promise((r) => setTimeout(r, 60)));
    expect(calls).toHaveLength(0);

    const hit = await screen.findByRole('option', { name: /Member of Technical Staff/ });
    expect(within(hit).getByText('Cerebrium')).toBeInTheDocument();
    expect(calls.length).toBeGreaterThan(0);
    expect((calls[0] ?? 0) - typed).toBeGreaterThanOrEqual(40); // 120 ms after the last key, less the time typing took
    expect((calls[0] ?? 0) - typed).toBeLessThan(300);

    // Two letters is too short to search postings.
    calls.length = 0;
    await user.clear(search());
    await user.type(search(), 'mt');
    await act(() => new Promise((r) => setTimeout(r, 200)));
    expect(calls).toHaveLength(0);
  });

  it('puts postings under Go to and Actions, and keeps them on screen while the next ones load', async () => {
    const user = userEvent.setup();
    const client = new FixtureClient();
    const real = client.searchPostings.bind(client);
    let release: (() => void) | undefined;
    const spy = vi.spyOn(client, 'searchPostings');
    await shell({ client });
    press('/');
    const dialog = await palette();
    await user.type(search(), 'mts');
    await screen.findByRole('option', { name: /Member of Technical Staff/ });
    const headings = [...dialog.querySelectorAll('[cmdk-group-heading]')].map((h) => h.textContent);
    expect(headings.slice(-1)).toEqual(['Postings']);
    expect(headings.indexOf('Actions')).toBeLessThan(headings.indexOf('Postings'));

    // The next search is slow: the old rows stay until it lands.
    spy.mockImplementation(async (q) => {
      await new Promise<void>((r) => (release = r));
      return real(q);
    });
    await user.type(search(), ' ');
    await user.type(search(), 'e');
    await waitFor(() => expect(release).toBeDefined());
    expect(screen.getByRole('option', { name: /Member of Technical Staff/ })).toBeInTheDocument();
    await act(async () => release?.());
  });

  it('shows the status of a person after the title and company', async () => {
    const user = userEvent.setup();
    await shell();
    press('/');
    await palette();
    await user.type(search(), 'priya');
    const row = await screen.findByRole('option', { name: /Priya Natarajan/ });
    const person = index.people.find((p) => p.name === 'Priya Natarajan');
    expect(within(row).getByText(`${person?.title}, ${person?.companyName}`)).toBeInTheDocument();
    expect(person?.statusLine).toBeTruthy();
    expect(within(row).getByText(person?.statusLine ?? '')).toHaveClass('text-muted-foreground');
  });

  it('says the index could not load instead of saying nothing matches', async () => {
    const user = userEvent.setup();
    const client = new FixtureClient();
    vi.spyOn(client, 'searchIndex').mockRejectedValue(new Error('down'));
    await shell({ client });
    press('/');
    await palette();
    expect(await screen.findByText('Search could not load. Nothing was changed. Try again.')).toBeInTheDocument();
    await user.type(search(), 'zzzzqqqq');
    expect(screen.queryByText(/Nothing matches/)).toBeNull();
    expect(screen.getByText('Search could not load. Nothing was changed. Try again.')).toBeInTheDocument();
  });

  it('says the posting search could not load', async () => {
    const user = userEvent.setup();
    const client = new FixtureClient();
    vi.spyOn(client, 'searchPostings').mockRejectedValue(new Error('down'));
    await shell({ client });
    press('/');
    await palette();
    await user.type(search(), 'mts');
    expect(await screen.findByText('Posting search could not load. Nothing was changed. Try again.')).toBeInTheDocument();
    expect(screen.queryByText('Search could not load. Nothing was changed. Try again.')).toBeNull();
  });

  it('Enter on a company goes to its page', async () => {
    const user = userEvent.setup();
    await shell();
    press('/');
    await palette();
    await user.type(search(), 'br');
    await screen.findByRole('option', { name: /^Browserbase/ });
    await waitFor(() => expect(screen.getByRole('option', { selected: true })).toHaveTextContent('Browserbase'));
    await user.keyboard('{Enter}');
    expect(where()).toBe(`/companies/${index.companies.find((c) => c.name === 'Browserbase')?.id}`);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('mod+Enter opens the company in the Outbox filtered to it', async () => {
    const user = userEvent.setup();
    await shell();
    press('/');
    await palette();
    await user.type(search(), 'br');
    await waitFor(() => expect(screen.getByRole('option', { selected: true })).toHaveTextContent('Browserbase'));
    fireEvent.keyDown(search(), { key: 'Enter', ctrlKey: true });
    expect(where()).toBe(`/outbox?company=${index.companies.find((c) => c.name === 'Browserbase')?.id}`);
  });

  it('Enter on a person opens the company on its People tab', async () => {
    const user = userEvent.setup();
    await shell();
    press('/');
    await palette();
    await user.type(search(), 'priya');
    const row = await screen.findByRole('option', { name: /Priya Natarajan/ });
    await user.click(row);
    const person = index.people.find((p) => p.name === 'Priya Natarajan');
    expect(where()).toBe(`/companies/${person?.companyId}?tab=people`);
  });

  it('goes to a view from Go to', async () => {
    const user = userEvent.setup();
    await shell();
    press('/');
    await palette();
    await user.type(search(), 'needs');
    await user.click(await screen.findByRole('option', { name: /Needs you/ }));
    expect(where()).toBe('/needs-you');
  });

  it('runs Toggle theme from Actions', async () => {
    const user = userEvent.setup();
    await shell();
    expect(document.documentElement).not.toHaveClass('dark');
    press('/');
    await palette();
    await user.type(search(), 'theme');
    await user.click(await screen.findByRole('option', { name: /Toggle theme/ }));
    expect(document.documentElement).toHaveClass('dark');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('pauses and resumes sending from Actions', async () => {
    const user = userEvent.setup();
    const client = new FixtureClient();
    const pause = vi.spyOn(client, 'pauseSending');
    const resume = vi.spyOn(client, 'resumeSending');
    await shell({ client });
    press('/');
    await palette();
    await user.type(search(), 'pause');
    await user.click(await screen.findByRole('option', { name: /Pause sending/ }));
    await waitFor(() => expect(pause).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('Sending paused. Nothing sends until you resume.')).toBeInTheDocument();

    press('/');
    await palette();
    await user.type(search(), 'resume');
    await user.click(await screen.findByRole('option', { name: /Resume sending/ }));
    await waitFor(() => expect(resume).toHaveBeenCalledWith(false, expect.anything()));
  });

  it('offers Pause only while running and Resume only while paused by you', async () => {
    const user = userEvent.setup();
    const client = new FixtureClient();
    await shell({ client });
    press('/');
    await palette();
    expect(screen.getByRole('option', { name: /Pause sending/ })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /Resume sending/ })).toBeNull();
    await user.click(screen.getByRole('option', { name: /Pause sending/ }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    const hard = new FixtureClient(buildFixture('hard_block'));
    cleanup();
    await shell({ client: hard });
    press('/');
    await palette();
    expect(screen.queryByRole('option', { name: /(Pause|Resume) sending/ })).toBeNull();
  });

  it('offers Pause wherever the sending strip does: outside the window and on the weekend too', async () => {
    const user = userEvent.setup();
    class Outside extends FixtureClient {
      override async shell() {
        const v = await super.shell();
        return { ...v, sending: { ...v.sending, state: 'outside_window' as const } };
      }
    }
    for (const client of [new Outside(), new FixtureClient(buildFixture('weekend'))]) {
      cleanup();
      await shell({ client });
      press('/');
      await palette();
      expect(screen.getByRole('option', { name: /Pause sending/ })).toBeInTheDocument();
      expect(screen.queryByRole('option', { name: /Resume sending/ })).toBeNull();
      await user.keyboard('{Escape}');
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    }
  });

  it('never offers Pause when sending is paused by you, signed out or hard-blocked', async () => {
    const paused = new FixtureClient();
    await paused.pauseSending({ requestId: 'setup' });
    for (const client of [paused, new FixtureClient(buildFixture('signed_out')), new FixtureClient(buildFixture('hard_block'))]) {
      cleanup();
      await shell({ client });
      press('/');
      await palette();
      expect(screen.queryByRole('option', { name: /Pause sending/ })).toBeNull();
    }
  });

  it('copies the picks for Claude', async () => {
    const user = userEvent.setup();
    const client = new FixtureClient();
    await shell({ client });
    press('/');
    await palette();
    await user.type(search(), 'copy');
    await user.click(await screen.findByRole('option', { name: /Copy picks for Claude/ }));
    expect(await screen.findByText('Copied')).toBeInTheDocument();
    const text = await navigator.clipboard.readText();
    const today = await client.today();
    const picked = today.proposed.filter((e) => e.state === 'picked');
    expect(picked.length).toBeGreaterThan(0);
    for (const entry of picked) expect(text).toContain(entry.company.name);
  });

  it('says so when nothing matches', async () => {
    const user = userEvent.setup();
    await shell();
    press('/');
    await palette();
    await user.type(search(), 'zzzzqqqq');
    expect(await screen.findByText('Nothing matches. Try a company, a person or a view name.')).toBeInTheDocument();
  });

  it('while the palette is open, a does nothing in the view behind', async () => {
    const run = vi.fn();
    await shell({ run });
    press('a');
    expect(run).toHaveBeenCalledTimes(1);

    press('/');
    await palette();
    press('a');
    fireEvent.keyDown(search(), { key: 'a' });
    expect(run).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(search(), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    press('a');
    expect(run).toHaveBeenCalledTimes(2);
  });

  it('opens from the sidebar Search button', async () => {
    const user = userEvent.setup();
    await shell();
    await user.click(screen.getByRole('button', { name: /^Search/ }));
    await palette();
  });
});

describe('ShortcutSheet', () => {
  it('? opens the sheet with Everywhere and the view group', async () => {
    await shell();
    press('?', { shiftKey: true });
    const sheet = await screen.findByRole('dialog', { name: 'Keyboard shortcuts' });
    expect(within(sheet).getByRole('heading', { name: 'Everywhere' })).toBeInTheDocument();
    expect(within(sheet).getByRole('heading', { name: 'Outbox' })).toBeInTheDocument();
    expect(within(sheet).getByText('Approve this email')).toBeInTheDocument();
    expect(within(sheet).getByText('Go to Today')).toBeInTheDocument();
    expect(within(sheet).getByText('Search and commands')).toBeInTheDocument();
  });

  it('shows the keys as caps, with a chord as two', async () => {
    await shell();
    press('?', { shiftKey: true });
    const sheet = await screen.findByRole('dialog', { name: 'Keyboard shortcuts' });
    const row = within(sheet).getByText('Go to Outbox').closest('li');
    const caps = [...(row?.querySelectorAll('[data-slot="kbd"]') ?? [])];
    expect(caps.map((k) => k.textContent)).toEqual(['G', 'O']);
  });

  it('opens from the top bar button and closes with Escape', async () => {
    const user = userEvent.setup();
    await shell();
    await user.click(screen.getByRole('button', { name: 'Keyboard shortcuts' }));
    await screen.findByRole('dialog', { name: 'Keyboard shortcuts' });
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('opens from the palette action', async () => {
    const user = userEvent.setup();
    await shell();
    press('/');
    await palette();
    await user.type(search(), 'shortcuts');
    await user.click(await screen.findByRole('option', { name: /Keyboard shortcuts/ }));
    await screen.findByRole('dialog', { name: 'Keyboard shortcuts' });
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
  });
});
