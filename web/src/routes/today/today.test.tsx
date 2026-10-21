import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { TodayView } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture, type Scenario } from '@/api/fixture/data';
import { SidebarProvider } from '@/components/ui/sidebar';
import { AppSidebar } from '@/shell/app-sidebar';
import { renderWithApp } from '@/test/render';
import TodayPage from './index';

/** Today next to the real sidebar, so the "N of 5" count by Today can be watched. */
function Page() {
  return (
    <SidebarProvider>
      <AppSidebar />
      <TodayPage />
    </SidebarProvider>
  );
}

async function open(client = new FixtureClient()) {
  const view = renderWithApp(<Page />, { route: '/today', client });
  await screen.findByRole('checkbox', { name: 'Pick Pylon' });
  return view;
}

async function openScenario(scenario: Scenario) {
  return renderWithApp(<Page />, { route: '/today', client: new FixtureClient(buildFixture(scenario)) });
}

const press = (key: string, init: KeyboardEventInit = {}) => fireEvent.keyDown(document.body, { key, ...init });
const sidebar = () => document.querySelector<HTMLElement>('[data-slot="sidebar"]') ?? document.body;
const todayCount = () => within(sidebar()).getByRole('link', { name: /^Today/ });
const box = (name: string) => screen.getByRole('checkbox', { name: `Pick ${name}` });
const rowOf = (name: string) => {
  const tr = box(name).closest('tr');
  if (!tr) throw new Error(`No row for ${name}`);
  return tr;
};
const select = (name: string) => fireEvent.click(rowOf(name));
const capacity = () => screen.getByLabelText('Capacity');
const capValue = (label: string) => within(capacity()).getByText(label).nextElementSibling;

async function entryOf(client: FixtureClient, name: string) {
  const t: TodayView = await client.today();
  const e = [...t.proposed, ...t.backlog].find((x) => x.company.name === name);
  if (!e) throw new Error(`No entry ${name}`);
  return e;
}

describe('Today', () => {
  it('heads the page with the date and shows capacity and the two groups', async () => {
    await open();
    expect(screen.getByRole('heading', { name: 'Tuesday, November 10' })).toBeInTheDocument();
    expect(capValue('Picked today')).toHaveTextContent('3 of 5');
    expect(capValue('Still emailing')).toHaveTextContent('5');
    expect(capValue('Proposed')).toHaveTextContent('18');
    expect(capValue('Backlog')).toHaveTextContent('41');

    const level = screen.getByText('Role at your level: new grad, early career, SWE I, SWE II').closest('tr');
    expect(level).toHaveTextContent('11');
    const anyRole = screen.getByText('Open US engineering role, level not named').closest('tr');
    expect(anyRole).toHaveTextContent('7');
  });

  it('lists the companies still being emailed', async () => {
    await open();
    const table = screen.getByRole('table', { name: 'Still emailing' });
    const browserbase = within(table).getByText('Browserbase').closest('tr');
    expect(browserbase).toHaveTextContent('Day 3 of 3');
    expect(browserbase).toHaveTextContent('6 of 7');
    expect(browserbase).toHaveTextContent('Answer Priya Natarajan in Gmail');
    expect(within(browserbase as HTMLElement).getByText('Held: reply')).toBeInTheDocument();
  });

  it('x picks up to capacity, then the checkbox explains the limit and nothing is called', async () => {
    const user = userEvent.setup();
    const { client } = await open();
    const pick = vi.spyOn(client, 'pick');
    const pylon = await entryOf(client as FixtureClient, 'Pylon');
    const baseten = await entryOf(client as FixtureClient, 'Baseten');

    select('Pylon');
    press('x');
    expect(within(todayCount()).getByText('4 of 5')).toBeInTheDocument();
    expect(await screen.findByText('Picked Pylon')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Undo' }).length).toBeGreaterThan(0);
    expect(box('Pylon')).toBeChecked();

    select('Baseten');
    press('x');
    expect(within(todayCount()).getByText('5 of 5')).toBeInTheDocument();
    expect(capValue('Picked today')).toHaveTextContent('5 of 5');

    const inngest = box('Inngest');
    expect(inngest).toHaveAttribute('aria-disabled', 'true');
    select('Inngest');
    press('x');
    expect(box('Inngest')).not.toBeChecked();
    expect(screen.queryByText('Picked Inngest')).not.toBeInTheDocument();

    await user.hover(box('Inngest'));
    expect(await screen.findByRole('tooltip')).toHaveTextContent("Today's limit carries 5 companies. Unpick one first.");

    // Leaving the page commits what waits in the undo window: Pylon and Baseten, never Inngest.
    act(() => {
      window.dispatchEvent(new Event('pagehide'));
    });
    await waitFor(() => expect(pick).toHaveBeenCalledTimes(2));
    expect(pick.mock.calls.map((c) => [c[0], c[1]])).toEqual([
      [pylon.company.id, pylon.revision],
      [baseten.company.id, baseten.revision],
    ]);
  });

  it('x on a picked company unpicks it', async () => {
    await open();
    select('Resend');
    press('x');
    expect(within(todayCount()).getByText('2 of 5')).toBeInTheDocument();
    expect(await screen.findByText('Unpicked Resend')).toBeInTheDocument();
  });

  it('the preview follows the selection', async () => {
    await open();
    select('Resend');
    const preview = screen.getByRole('complementary', { name: 'Resend preview' });
    expect(within(preview).getAllByText('Software Engineer, New Grad').length).toBeGreaterThan(0);
    expect(within(preview).getByText('38 people')).toBeInTheDocument();
    expect(within(preview).getByText('Has sponsored H-1B')).toBeInTheDocument();
    expect(within(preview).getByText('Picked')).toBeInTheDocument();

    press('j');
    expect(await screen.findByRole('complementary', { name: 'Cerebrium preview' })).toBeInTheDocument();
    expect(screen.queryByRole('complementary', { name: 'Resend preview' })).not.toBeInTheDocument();
  });

  it('r opens the remove dialog with four reasons, and Remove calls removeCompany', async () => {
    const { client } = await open();
    const remove = vi.spyOn(client, 'removeCompany');
    const pylon = await entryOf(client as FixtureClient, 'Pylon');

    select('Pylon');
    press('r');
    const dialog = await screen.findByRole('dialog', { name: 'Remove Pylon for good?' });
    expect(within(dialog).getAllByRole('radio')).toHaveLength(4);
    for (const name of ['Not a fit for me', 'Already applied or in touch another way', 'Bigger than it looks', 'Other']) {
      expect(within(dialog).getByRole('radio', { name })).toBeInTheDocument();
    }
    expect(within(dialog).getByRole('radio', { name: 'Not a fit for me' })).toBeChecked();
    fireEvent.click(within(dialog).getByRole('radio', { name: 'Bigger than it looks' }));
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Note' }), { target: { value: 'Raised a big round' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));

    await waitFor(() => expect(remove).toHaveBeenCalledTimes(1));
    expect(remove).toHaveBeenCalledWith(pylon.company.id, 'bigger_than_it_looks', 'Raised a big round', pylon.revision, expect.objectContaining({ requestId: expect.any(String) }));
    expect(await screen.findByText('Removed Pylon. Find it on the No-go list.')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('checkbox', { name: 'Pick Pylon' })).not.toBeInTheDocument());
  });

  it('shift+c copies the handoff for every pick', async () => {
    await open();
    const writeText = vi.fn<(text: string) => Promise<void>>(async () => {});
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });

    press('C', { shiftKey: true });
    expect(await screen.findByText('Copied 3 companies for the side terminal.')).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledTimes(1);
    const text = writeText.mock.calls[0]?.[0] ?? '';
    expect(text).toMatch(/^Add people for Resend \(company \d+\)\.\nRole to name: Software Engineer, New Grad\.\n\n/);
    expect(text).toContain('Add people for Cerebrium');
    expect(text).toContain('Add people for Trigger.dev');
    expect(text).not.toContain('Pylon');
  });

  it('c copies the role title', async () => {
    await open();
    const writeText = vi.fn<(text: string) => Promise<void>>(async () => {});
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    select('Baseten');
    press('c');
    expect(await screen.findByText('Copied: Forward Deployed Engineer, New Grad')).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledWith('Forward Deployed Engineer, New Grad');
  });

  it('b sends the company to the backlog with Undo, and 2 opens the Backlog tab', async () => {
    await open();
    select('Warp');
    press('b');
    expect(await screen.findByText('Moved Warp to the backlog')).toBeInTheDocument();
    // The row stays where it was, relabelled, and the selection moves on.
    expect(within(rowOf('Warp')).getByText('Backlog')).toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: 'Zed Industries preview' })).toBeInTheDocument();

    press('2');
    expect(await screen.findByRole('tab', { name: /Backlog/, selected: true })).toBeInTheDocument();
    expect(box('Modal')).toBeInTheDocument();
    press('1');
    expect(await screen.findByRole('tab', { name: /Proposed/, selected: true })).toBeInTheDocument();
  });

  it('shows the amber Unknown size, Starts 2027 and Unclear', async () => {
    await open();
    const unknown = within(rowOf('Trigger.dev')).getByText('Unknown');
    expect(unknown.closest('[data-slot="badge"]')).toHaveAttribute('data-variant', 'warn');
    const hex = rowOf('Hex');
    expect(within(hex).getByText('Starts 2027').closest('[data-slot="badge"]')).toHaveAttribute('data-variant', 'warn');
    expect(within(hex).getByText('Unclear').closest('[data-slot="badge"]')).toHaveAttribute('data-variant', 'warn');
  });

  it('weekend: the not-ready text comes from the client', async () => {
    await openScenario('weekend');
    expect(await screen.findByText('No list on weekends. The backlog is still here.')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Saturday, November 14' })).toBeInTheDocument();
  });

  it('job_failed: the not-ready text, the stale banner and the job error', async () => {
    await openScenario('job_failed');
    expect(await screen.findByText("Today's list is not ready. It is built at 08:00 on weekdays.")).toBeInTheDocument();
    const banner = screen.getByRole('alert');
    expect(banner).toHaveTextContent('Daily list failed');
    expect(banner).toHaveTextContent('Could not read the company store: database is locked');
  });

  describe('opposite toggles inside the undo window', () => {
    const flushPage = () =>
      act(() => {
        window.dispatchEvent(new Event('pagehide'));
      });
    const idle = (ms = 40) => act(() => new Promise<void>((r) => setTimeout(r, ms)));

    it('pick then unpick sends nothing, even when the page flushes', async () => {
      const { client } = await open();
      const pick = vi.spyOn(client, 'pick');
      const unpick = vi.spyOn(client, 'unpick');
      const pylon = await entryOf(client as FixtureClient, 'Pylon');

      select('Pylon');
      press('x');
      expect(box('Pylon')).toBeChecked();
      expect(within(todayCount()).getByText('4 of 5')).toBeInTheDocument();
      press('x');
      expect(box('Pylon')).not.toBeChecked();
      expect(within(todayCount()).getByText('3 of 5')).toBeInTheDocument();
      expect(screen.queryByText('Picked Pylon')).not.toBeInTheDocument();

      flushPage();
      await idle();
      expect(pick).not.toHaveBeenCalled();
      expect(unpick).not.toHaveBeenCalled();
      expect(screen.queryByText(/changed since you loaded it/)).not.toBeInTheDocument();
      expect(await entryOf(client as FixtureClient, 'Pylon')).toMatchObject({ state: 'proposed', revision: pylon.revision });
    });

    it('unpick then pick again on a picked company sends nothing', async () => {
      const { client } = await open();
      const pick = vi.spyOn(client, 'pick');
      const unpick = vi.spyOn(client, 'unpick');

      select('Resend');
      press('x');
      expect(box('Resend')).not.toBeChecked();
      press('x');
      expect(box('Resend')).toBeChecked();
      expect(within(todayCount()).getByText('3 of 5')).toBeInTheDocument();

      flushPage();
      await idle();
      expect(pick).not.toHaveBeenCalled();
      expect(unpick).not.toHaveBeenCalled();
    });

    it('pick, unpick, pick ends picked with one pick call after 8 seconds', async () => {
      const { client } = await open();
      const pick = vi.spyOn(client, 'pick');
      const unpick = vi.spyOn(client, 'unpick');
      const pylon = await entryOf(client as FixtureClient, 'Pylon');

      vi.useFakeTimers();
      try {
        const settle = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));
        select('Pylon');
        press('x');
        press('x');
        press('x');
        await settle(0);
        expect(box('Pylon')).toBeChecked();
        expect(within(todayCount()).getByText('4 of 5')).toBeInTheDocument();
        // The toasts of the two cancelled writes are dismissed; one live "Picked Pylon" with Undo stays.
        await settle(1000);
        const live = screen.getAllByText('Picked Pylon').filter((el) => el.closest('[data-sonner-toast]')?.getAttribute('data-removed') !== 'true');
        expect(live).toHaveLength(1);

        await settle(6900);
        expect(pick).not.toHaveBeenCalled();
        await settle(200);
        expect(pick).toHaveBeenCalledTimes(1);
        expect(pick).toHaveBeenCalledWith(pylon.company.id, pylon.revision, expect.objectContaining({ requestId: expect.any(String) }));
        expect(unpick).not.toHaveBeenCalled();
        await settle(500);
        expect(box('Pylon')).toBeChecked();
        expect(await entryOf(client as FixtureClient, 'Pylon')).toMatchObject({ state: 'picked' });
      } finally {
        vi.useRealTimers();
      }
    });

    it('an unpick after a committed pick uses the revision the pick returned, not the cached one', async () => {
      const { client } = await open();
      const pick = vi.spyOn(client, 'pick');
      const unpick = vi.spyOn(client, 'unpick');
      const pylon = await entryOf(client as FixtureClient, 'Pylon');
      // The refetch after the pick never lands, so the cache keeps the old revision.
      vi.spyOn(client, 'today').mockImplementation(() => new Promise(() => {}));

      select('Pylon');
      press('x');
      flushPage();
      await waitFor(() => expect(pick).toHaveBeenCalledTimes(1));
      await pick.mock.results[0]?.value;
      await idle();

      press('x');
      expect(box('Pylon')).not.toBeChecked();
      flushPage();
      await waitFor(() => expect(unpick).toHaveBeenCalledTimes(1));
      expect(unpick.mock.calls[0]?.[1]).toBe(pylon.revision + 1);
      await idle();
      expect(screen.queryByText(/changed since you loaded it/)).not.toBeInTheDocument();
    });

    it('remove after a pending pick drops the pick and removes with the row revision', async () => {
      const { client } = await open();
      const pick = vi.spyOn(client, 'pick');
      const remove = vi.spyOn(client, 'removeCompany');
      const pylon = await entryOf(client as FixtureClient, 'Pylon');

      select('Pylon');
      press('x');
      expect(within(todayCount()).getByText('4 of 5')).toBeInTheDocument();
      press('r');
      const dialog = await screen.findByRole('dialog', { name: 'Remove Pylon for good?' });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));

      await waitFor(() => expect(remove).toHaveBeenCalledTimes(1));
      expect(remove).toHaveBeenCalledWith(pylon.company.id, 'not_a_fit', '', pylon.revision, expect.objectContaining({ requestId: expect.any(String) }));
      await waitFor(() => expect(screen.queryByRole('checkbox', { name: 'Pick Pylon' })).not.toBeInTheDocument());
      expect(within(todayCount()).getByText('3 of 5')).toBeInTheDocument();

      flushPage();
      await idle();
      expect(pick).not.toHaveBeenCalled();
      expect(screen.queryByText(/changed since you loaded it/)).not.toBeInTheDocument();
    });
  });

  it('j and k walk the rows in the order the table shows them, group by group', async () => {
    const data = buildFixture();
    const at = data.today.proposed.findIndex((e) => e.company.name === 'Railway');
    const [railway] = data.today.proposed.splice(at, 1);
    if (!railway) throw new Error('No Railway in the fixture');
    data.today.proposed.splice(1, 0, railway);
    renderWithApp(<Page />, { route: '/today', client: new FixtureClient(data) });
    await screen.findByRole('checkbox', { name: 'Pick Pylon' });

    select('Pylon');
    press('j');
    // The API lists Railway second, but the table shows it with the open-role group, below Fly.io.
    expect(await screen.findByRole('complementary', { name: 'Resend preview' })).toBeInTheDocument();
    press('k');
    expect(await screen.findByRole('complementary', { name: 'Pylon preview' })).toBeInTheDocument();
    select('Fly.io');
    press('j');
    expect(await screen.findByRole('complementary', { name: 'Railway preview' })).toBeInTheDocument();
  });
});
