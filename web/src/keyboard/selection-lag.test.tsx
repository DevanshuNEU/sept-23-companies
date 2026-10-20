import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { useState } from 'react';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture, ids, messageOf } from '@/api/fixture/data';
import { SidebarProvider } from '@/components/ui/sidebar';
import LinkedInPage from '@/routes/linkedin';
import OutboxPage from '@/routes/outbox';
import TodayPage from '@/routes/today';
import { AppSidebar } from '@/shell/app-sidebar';
import { renderWithApp } from '@/test/render';
import { HotkeysProvider, useHotkeys, useSelectionSync } from './hotkeys';

/**
 * A key that lands before the selection it follows has committed must still act on the row he moved to.
 * Every key here goes in the same `act()` as the move, so no render happens in between.
 */

const press = (key: string) => fireEvent.keyDown(document.body, { key });
const sameTick = (...steps: (() => void)[]) =>
  act(() => {
    for (const step of steps) step();
  });

describe('a key right after a selection change acts on the new row', () => {
  afterEach(async () => {
    if (vi.isFakeTimers()) {
      await act(() => vi.runOnlyPendingTimersAsync());
      vi.useRealTimers();
    }
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it('Outbox: j then a approves the draft he moved to, not the one he left', async () => {
    const client = new FixtureClient(buildFixture());
    renderWithApp(<OutboxPage />, { route: `/outbox/${ids.touch.omar}`, client });
    await screen.findByRole('heading', { level: 2, name: 'Omar Haddad' });

    // The draft below Omar in the list, and its name.
    const options = [...document.querySelectorAll<HTMLElement>('[role="option"][data-touch]')];
    const at = options.findIndex((o) => o.dataset.touch === String(ids.touch.omar));
    const next = options[at + 1];
    if (!next) throw new Error('no row below Omar');
    const nextTouch = Number(next.dataset.touch);
    const nextName = next.querySelector('b')?.textContent ?? '';

    // Visit the row below once so its detail is cached and shows in the same render as the selection.
    press('j');
    await screen.findByRole('heading', { level: 2, name: nextName });
    press('k');
    await screen.findByRole('heading', { level: 2, name: 'Omar Haddad' });
    expect(within(screen.getByRole('button', { name: 'Approve' }).closest('article') as HTMLElement).getByRole('heading', { level: 2 })).toHaveTextContent('Omar Haddad');

    const approve = vi.spyOn(client, 'approve');
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'requestAnimationFrame', 'cancelAnimationFrame'] });
    sameTick(
      () => press('j'),
      () => press('a'),
    );
    await act(() => vi.advanceTimersByTimeAsync(8000));

    expect(approve).toHaveBeenCalledTimes(1);
    expect(approve.mock.calls[0]?.[0]).toBe(messageOf(nextTouch));
    expect(approve.mock.calls.some((c) => c[0] === messageOf(ids.touch.omar))).toBe(false);
  });

  it('Today: j then x picks the row he moved to and leaves the picked row he left alone', async () => {
    renderWithApp(
      <SidebarProvider>
        <AppSidebar />
        <TodayPage />
      </SidebarProvider>,
      { route: '/today', client: new FixtureClient() },
    );
    await screen.findByRole('checkbox', { name: 'Pick Pylon' });
    const boxes = () => screen.getAllByRole('checkbox', { name: /^Pick / });
    const nameOf = (b: HTMLElement) => (b.getAttribute('aria-label') ?? '').replace(/^Pick /, '');
    // A picked row with an unpicked row right below it: pressing x on the wrong one unpicks instead of picks.
    const list = boxes();
    const at = list.findIndex((b, i) => (b as HTMLInputElement).getAttribute('aria-checked') === 'true' && list[i + 1]?.getAttribute('aria-checked') !== 'true');
    const above = list[at];
    const below = list[at + 1];
    if (!above || !below) throw new Error('no picked row above an unpicked one');
    const aboveName = nameOf(above);
    const belowName = nameOf(below);
    expect(above).toBeChecked();
    expect(below).not.toBeChecked();

    fireEvent.click(above.closest('tr') as HTMLElement);
    sameTick(
      () => press('j'),
      () => press('x'),
    );

    expect(await screen.findByText(`Picked ${belowName}`)).toBeInTheDocument();
    expect(screen.queryByText(`Unpicked ${aboveName}`)).not.toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: `Pick ${belowName}` })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: `Pick ${aboveName}` })).toBeChecked();
  });

  it('LinkedIn: a click on a row then r logs for that row', async () => {
    const client = new FixtureClient();
    renderWithApp(<LinkedInPage />, { route: '/linkedin', client });
    await screen.findByText('Hannah Cole');
    const spy = vi.spyOn(client, 'logLinkedIn');
    const table = screen.getByRole('table', { hidden: true });
    const row = (name: string) => within(table).getByText(name, { exact: true }).closest('tr') as HTMLElement;

    fireEvent.click(row('Hannah Cole'));
    sameTick(
      () => fireEvent.click(row('Owen Clarke')),
      () => press('r'),
    );

    expect(await screen.findByText('Logged: request sent to Owen Clarke.')).toBeInTheDocument();
    expect(screen.queryByText('Logged: request sent to Hannah Cole.')).not.toBeInTheDocument();
    expect(row('Hannah Cole')).toHaveTextContent('Not started');
    // Leaving the page commits what waits in the undo window: one log, and it is Owen's.
    act(() => {
      window.dispatchEvent(new Event('pagehide'));
    });
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    const owen = (await client.linkedin()).groups.flatMap((g) => g.people).find((x) => x.person.name === 'Owen Clarke');
    expect(spy.mock.calls[0]?.[0]).toBe(owen?.person.id);
  });
});

/** The rule itself, on a bare list: rows 1 to 3, and `a` is switched off for row 2. */
function Rows({ log }: { log: (what: string) => void }) {
  const [selected, setSelected] = useState(1);
  const sync = useSelectionSync(selected);
  const go = (id: number) => {
    sync.move(id);
    setSelected(id);
  };
  useHotkeys([{ keys: 'j', label: 'Next', group: 'Test', run: () => go(Math.min(sync.latest() ?? 1, 2) + 1) }]);
  useHotkeys(
    [
      { keys: 'a', label: 'Act', group: 'Test', enabled: selected !== 2, run: () => log(`a on ${selected}`) },
      { keys: 'b', label: 'Other', group: 'Test', run: () => log(`b on ${selected}`) },
    ],
    { selection: selected },
  );
  useHotkeys([{ keys: 'q', label: 'Any row', group: 'Test', run: () => log(`q on ${selected}`) }]);
  return <p data-testid="selected">{selected}</p>;
}

function renderRows() {
  const log = vi.fn();
  render(
    <HotkeysProvider>
      <Rows log={log} />
    </HotkeysProvider>,
  );
  return log;
}

describe('the hotkey layer', () => {
  it('a key with no move on the way runs at once on the row on screen', () => {
    const log = renderRows();
    press('a');
    expect(log).toHaveBeenCalledWith('a on 1');
  });

  it('a key after a move acts on the new row once it has rendered, and only once', () => {
    const log = renderRows();
    sameTick(
      () => press('j'),
      () => press('b'),
    );
    expect(screen.getByTestId('selected')).toHaveTextContent('2');
    expect(log.mock.calls).toEqual([['b on 2']]);
  });

  it('a key the new row does not allow is dropped, not run on the row he left', () => {
    const log = renderRows();
    sameTick(
      () => press('j'),
      () => press('a'),
    );
    expect(screen.getByTestId('selected')).toHaveTextContent('2');
    expect(log).not.toHaveBeenCalled();
  });

  it('a key that does not depend on the row runs at once, before the move renders', () => {
    const log = renderRows();
    sameTick(
      () => press('j'),
      () => {
        press('q');
        expect(log.mock.calls).toEqual([['q on 1']]);
      },
    );
  });

  it('a key held for a row that never rendered (he moved on in the same tick) is dropped; the last one acts on its own row', () => {
    const log = renderRows();
    sameTick(
      () => press('j'),
      () => press('b'),
      () => press('j'),
      () => press('b'),
    );
    expect(screen.getByTestId('selected')).toHaveTextContent('3');
    expect(log.mock.calls.map((c) => c[0]).filter((x) => x.startsWith('b'))).toEqual(['b on 3']);
  });
});
