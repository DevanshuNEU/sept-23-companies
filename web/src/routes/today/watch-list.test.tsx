import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { toast } from 'sonner';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture, type FixtureDb, type Scenario } from '@/api/fixture/data';
import { AppShell } from '@/shell/app-shell';
import { renderWithApp } from '@/test/render';
import TodayPage from './index';
import { WatchList } from './watch-list';

const press = (key: string) => fireEvent.keyDown(document.body, { key });

/** Leaving the page commits what waits in the undo window, at once. */
const leavePage = () =>
  act(() => {
    window.dispatchEvent(new Event('pagehide'));
  });

async function open(o: { db?: FixtureDb; scenario?: Scenario } = {}) {
  const db = o.db ?? buildFixture(o.scenario ?? 'default');
  const client = new FixtureClient(db);
  const view = renderWithApp(
    <>
      <AppShell />
      <WatchList />
    </>,
    { client },
  );
  return { ...view, client, db };
}

const section = () => screen.getByRole('region', { name: /Watch list/ });
/** The list is closed on arrival: open it to read the items. */
async function openList() {
  await screen.findByRole('region', { name: /Watch list/ });
  fireEvent.click(within(section()).getByRole('button', { name: /Watch list/ }));
  return within(section());
}
const item = (name: RegExp | string) => {
  const li = within(section()).getByText(name).closest('li');
  if (!li) throw new Error(`No item for ${String(name)}`);
  return within(li);
};

afterEach(() => {
  toast.dismiss();
  window.localStorage.clear();
});

describe('WatchList', () => {
  it('is closed on arrival, with a plain grey count', async () => {
    await open();
    await screen.findByRole('region', { name: /Watch list/ });
    const trigger = within(section()).getByRole('button', { name: /Watch list/ });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(within(section()).queryByRole('list')).not.toBeInTheDocument();
    const count = within(section()).getByTestId('count');
    expect(count).toHaveTextContent('3');
    expect(count.closest('[data-slot="badge"]')).toBeNull();
    expect(count.className).toMatch(/text-muted-foreground/);
    expect(count.className).not.toMatch(/bad|red|warn/);
  });

  it('lists one item of each kind with its reason, company and date', async () => {
    await open();
    const list = await openList();
    expect(list.getAllByRole('listitem')).toHaveLength(3);

    const careers = item('Careers page changed');
    expect(careers.getByRole('link', { name: 'Pylon' })).toHaveAttribute('href', expect.stringMatching(/^\/companies\/\d+$/));
    expect(careers.getByText('The careers page moved to a new address. The old one lists no roles.')).toBeInTheDocument();
    expect(careers.getByText('Nov 9')).toBeInTheDocument();

    const fresh = item('New company to reach');
    expect(fresh.getByText('Mintlify announced a Series A on Nov 5. You have a reason to write without a role.')).toBeInTheDocument();
    expect(fresh.getByText('Nov 6')).toBeInTheDocument();

    const merge = item('Possible duplicate');
    expect(merge.getByText('Hex and Hex Technologies may be one company. They share a careers page.')).toBeInTheDocument();
    expect(merge.getByText('Nov 8')).toBeInTheDocument();
  });

  it('a careers item opens the careers link in a new tab with noopener', async () => {
    const { db } = await open();
    await openList();
    const source = db.watch.items.find((i) => i.kind === 'careers_changed')?.link;
    if (!source) throw new Error('fixture: a careers item with a link');
    const link = item('Careers page changed').getByRole('link', { name: source.label });
    expect(link).toHaveAttribute('href', source.url);
    expect(link).toHaveAttribute('target', '_blank');
    expect(link.getAttribute('rel')).toMatch(/noopener/);
    expect(link.getAttribute('rel')).toMatch(/noreferrer/);
  });

  it('a new company item links to the company page', async () => {
    const { db } = await open();
    await openList();
    const mintlify = db.watch.items.find((i) => i.kind === 'new_reason_no_role');
    if (!mintlify) throw new Error('fixture: a new company item');
    const link = item('New company to reach').getByRole('link', { name: "Open Mintlify's page" });
    expect(link).toHaveAttribute('href', `/companies/${mintlify.company.id}`);
    expect(link).not.toHaveAttribute('target');
  });

  it('a merge item links to /needs-you', async () => {
    await open();
    await openList();
    expect(item('Possible duplicate').getByRole('link', { name: 'Decide in Needs you' })).toHaveAttribute('href', '/needs-you');
  });

  it('dismiss removes the item at once, and undo brings it back with nothing sent', async () => {
    const { client } = await open();
    await openList();
    const dismiss = vi.spyOn(client, 'dismissWatch');
    fireEvent.click(item('Careers page changed').getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByText('Careers page changed')).not.toBeInTheDocument();
    expect(within(section()).getByTestId('count')).toHaveTextContent('2');
    expect(await screen.findByText('Dismissed: Pylon.')).toBeInTheDocument();
    press('z');
    await waitFor(() => expect(screen.getByText('Careers page changed')).toBeInTheDocument());
    expect(within(section()).getByTestId('count')).toHaveTextContent('3');
    await leavePage();
    expect(dismiss).not.toHaveBeenCalled();
  });

  it('a dismissal that is left alone is sent once the window ends', async () => {
    const { client, db } = await open();
    await openList();
    const dismiss = vi.spyOn(client, 'dismissWatch');
    fireEvent.click(item('Possible duplicate').getByRole('button', { name: 'Dismiss' }));
    await leavePage();
    await waitFor(() => expect(dismiss).toHaveBeenCalledTimes(1));
    expect(dismiss.mock.calls[0]?.[0]).toBe(7103);
    await waitFor(() => expect(db.watch.items.map((i) => i.id)).toEqual([7101, 7102]));
  });

  it('the last dismissal takes the whole section away', async () => {
    const db = buildFixture();
    db.watch.items = db.watch.items.slice(0, 1);
    await open({ db });
    await openList();
    fireEvent.click(within(section()).getByRole('button', { name: 'Dismiss' }));
    await waitFor(() => expect(screen.queryByRole('region', { name: /Watch list/ })).not.toBeInTheDocument());
  });

  it('an unsafe link renders as text, not a link', async () => {
    const db = buildFixture();
    const careers = db.watch.items.find((i) => i.kind === 'careers_changed');
    if (!careers?.link) throw new Error('fixture: a careers item with a link');
    careers.link = { label: 'Pylon careers page', url: 'javascript:alert(1)' };
    await open({ db });
    await openList();
    const row = item('Careers page changed');
    expect(row.queryByRole('link', { name: 'Pylon careers page' })).not.toBeInTheDocument();
    expect(row.getByText('Pylon careers page')).toBeInTheDocument();
    expect(section().innerHTML).not.toContain('javascript:');
  });

  it('nothing renders with no items', async () => {
    await open({ scenario: 'empty' });
    // Let the read settle: nothing may appear once it has.
    await act(() => new Promise((r) => setTimeout(r, 30)));
    expect(screen.queryByRole('region', { name: /Watch list/ })).not.toBeInTheDocument();
  });

  it('has no guilt word and no alarm tone', async () => {
    await open();
    const list = await openList();
    expect(list.getAllByRole('listitem').length).toBeGreaterThan(0);
    const tokens = [...section().querySelectorAll('*')].flatMap((el) => [...el.classList]);
    expect(tokens.filter((c) => /^(?:text|bg|border)-(?:bad|red)/.test(c))).toEqual([]);
    expect(section().textContent).not.toMatch(/overdue|behind|missed|streak|keep it up/i);
  });

  describe('on Today', () => {
    it('sits below the 7-day question', async () => {
      renderWithApp(
        <>
          <AppShell />
          <TodayPage />
        </>,
        { client: new FixtureClient(buildFixture()) },
      );
      const prompt = await screen.findByRole('region', { name: /Last week, what happened/ });
      const watch = await screen.findByRole('region', { name: /Watch list/ });
      expect(prompt.compareDocumentPosition(watch)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    });
  });
});
