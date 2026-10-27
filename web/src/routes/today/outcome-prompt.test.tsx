import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { toast } from 'sonner';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture, type FixtureDb, type Scenario } from '@/api/fixture/data';
import { AppShell } from '@/shell/app-shell';
import { renderWithApp } from '@/test/render';
import TodayPage from './index';
import { OutcomePrompt } from './outcome-prompt';

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
      <OutcomePrompt />
    </>,
    { client },
  );
  return { ...view, client, db };
}

const section = () => screen.getByRole('region', { name: /Last week, what happened/ });
const question = () => within(section()).getByTestId('question');
const OWEN = 'What happened with Owen Brandt at Warp? You sent a request on Oct 30.';
const MARTA = 'What happened with Marta Kowalski at Hex? You sent a request on Nov 2.';

afterEach(() => {
  toast.dismiss();
  window.localStorage.clear();
});

describe('OutcomePrompt', () => {
  it('asks about the oldest touch first with its date and context', async () => {
    await open();
    await screen.findByRole('region', { name: /Last week, what happened/ });
    expect(question()).toHaveTextContent(OWEN);
    expect(within(section()).getByText('Request with a note about the new terminal blocks')).toBeInTheDocument();
    // One question at a time, with the number waiting as plain grey text.
    expect(within(section()).getByTestId('count')).toHaveTextContent('2');
    expect(within(section()).getByText('1 more after this one. Nothing yet is a normal answer: it asks again in 7 days.')).toBeInTheDocument();
    const keys = within(section())
      .getAllByRole('button')
      .map((b) => b.textContent);
    expect(keys).toEqual(expect.arrayContaining(['Accepted1', 'Replied2', 'Nothing yet3']));
  });

  it('is shown open, and a collapsed section hides the question and its keys', async () => {
    const { client } = await open();
    await screen.findByRole('region', { name: /Last week, what happened/ });
    const trigger = within(section()).getByRole('button', { name: /Last week, what happened/ });
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(within(section()).queryByTestId('question')).not.toBeInTheDocument();
    const log = vi.spyOn(client, 'logOutcome');
    press('1');
    await leavePage();
    expect(log).not.toHaveBeenCalled();
  });

  it('1 logs accepted, the question moves to the next touch, and a win appears in the wins view', async () => {
    const { client } = await open();
    await screen.findByRole('region', { name: /Last week, what happened/ });
    const before = (await client.wins()).wins.length;
    press('1');
    expect(question()).toHaveTextContent(MARTA);
    expect(within(section()).getByTestId('count')).toHaveTextContent('1');
    expect(within(section()).getByText('Last one. Nothing yet is a normal answer: it asks again in 7 days.')).toBeInTheDocument();
    expect(await screen.findByText('Logged: Owen Brandt accepted.')).toBeInTheDocument();
    await leavePage();
    await waitFor(async () => expect((await client.wins()).wins).toHaveLength(before + 1));
    const win = (await client.wins()).wins[0];
    expect(win).toMatchObject({ kind: 'accepted', unseen: true });
    expect(win?.line).toBe('Owen Brandt at Warp accepted your request.');
  });

  it('3 logs quiet and adds no win', async () => {
    const { client } = await open();
    await screen.findByRole('region', { name: /Last week, what happened/ });
    const before = (await client.wins()).wins.length;
    const log = vi.spyOn(client, 'logOutcome');
    press('3');
    expect(question()).toHaveTextContent(MARTA);
    expect(await screen.findByText('Logged: nothing yet from Owen Brandt.')).toBeInTheDocument();
    await leavePage();
    await waitFor(() => expect(log).toHaveBeenCalledTimes(1));
    expect(log.mock.calls[0]?.slice(0, 2)).toEqual([2202, 'quiet']);
    expect((await client.wins()).wins).toHaveLength(before);
  });

  it('2 opens the reply dialog, and Interested logs replied with kind interested', async () => {
    const { client } = await open();
    await screen.findByRole('region', { name: /Last week, what happened/ });
    const log = vi.spyOn(client, 'logOutcome');
    press('2');
    const dialog = await screen.findByRole('dialog', { name: 'What kind of reply?' });
    expect(dialog).toHaveTextContent('From Owen Brandt on LinkedIn.');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Interested' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(question()).toHaveTextContent(MARTA);
    await leavePage();
    await waitFor(() => expect(log).toHaveBeenCalledTimes(1));
    expect(log.mock.calls[0]?.[1]).toBe('replied');
    expect(log.mock.calls[0]?.[4]).toBe('interested');
  });

  it('cancelling the reply dialog logs nothing and keeps the question', async () => {
    const { client } = await open();
    await screen.findByRole('region', { name: /Last week, what happened/ });
    const log = vi.spyOn(client, 'logOutcome');
    press('2');
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(question()).toHaveTextContent(OWEN);
    await leavePage();
    expect(log).not.toHaveBeenCalled();
  });

  it('said no commits at once, with no undo window', async () => {
    const { client } = await open();
    await screen.findByRole('region', { name: /Last week, what happened/ });
    const log = vi.spyOn(client, 'logOutcome');
    press('2');
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Said no' }));
    await waitFor(() => expect(log).toHaveBeenCalledTimes(1));
    expect(log.mock.calls[0]?.[4]).toBe('said_no');
    // No Undo on the toast, and no win for a refusal.
    expect(screen.queryByRole('button', { name: 'Undo' })).not.toBeInTheDocument();
    expect((await client.wins()).wins.some((w) => w.company.name === 'Warp')).toBe(false);
  });

  it('undo within the window brings the question back, and nothing is sent', async () => {
    const { client } = await open();
    await screen.findByRole('region', { name: /Last week, what happened/ });
    const log = vi.spyOn(client, 'logOutcome');
    press('1');
    expect(question()).toHaveTextContent(MARTA);
    press('z');
    await waitFor(() => expect(question()).toHaveTextContent(OWEN));
    expect(within(section()).getByTestId('count')).toHaveTextContent('2');
    await leavePage();
    expect(log).not.toHaveBeenCalled();
  });

  it('answering the last touch removes the section', async () => {
    const db = buildFixture();
    db.prompt.touches = db.prompt.touches.slice(0, 1);
    await open({ db });
    await screen.findByRole('region', { name: /Last week, what happened/ });
    press('3');
    await waitFor(() => expect(screen.queryByRole('region', { name: /Last week, what happened/ })).not.toBeInTheDocument());
  });

  it('is absent with no touches', async () => {
    await open({ scenario: 'fresh' });
    // Let the read settle: nothing may appear once it has.
    await act(() => new Promise((r) => setTimeout(r, 30)));
    expect(screen.queryByRole('region', { name: /Last week, what happened/ })).not.toBeInTheDocument();
  });

  it('the keys do nothing while a dialog is open', async () => {
    const { client } = await open();
    await screen.findByRole('region', { name: /Last week, what happened/ });
    const log = vi.spyOn(client, 'logOutcome');
    press('2');
    await screen.findByRole('dialog');
    press('1');
    press('3');
    await leavePage();
    expect(log).not.toHaveBeenCalled();
    // The dialog hides the page from the accessibility tree, so the question is read by its test id.
    expect(screen.getByTestId('question')).toHaveTextContent(OWEN);
  });

  it('names an application by its company, since it has no person', async () => {
    const db = buildFixture();
    const first = db.prompt.touches[1];
    if (!first) throw new Error('fixture: two touches');
    first.person = null;
    first.channel = 'application';
    first.context = 'Applied for Software Engineer I';
    await open({ db });
    await screen.findByRole('region', { name: /Last week, what happened/ });
    expect(question()).toHaveTextContent('What happened with your application at Warp? You sent an application on Oct 30.');
  });

  it('has no red count, no tone classes and no guilt word', async () => {
    await open();
    await screen.findByRole('region', { name: /Last week, what happened/ });
    const tokens = [...section().querySelectorAll('*')].flatMap((el) => [...el.classList]);
    expect(tokens.filter((c) => /^(?:text|bg|border)-(?:bad|red)/.test(c))).toEqual([]);
    expect(section().querySelector('[data-slot="badge"]')).toBeNull();
    expect(section().textContent).not.toMatch(/overdue|behind|missed|streak|keep it up/i);
  });

  describe('on Today', () => {
    it('sits under the selected person, and answering from there works', async () => {
      const client = new FixtureClient(buildFixture());
      renderWithApp(
        <>
          <AppShell />
          <TodayPage />
        </>,
        { client },
      );
      await screen.findByRole('heading', { level: 2, name: 'Lena Ortiz' });
      const person = screen.getByRole('region', { name: 'Selected person' });
      const prompt = await within(person).findByRole('region', { name: /Last week, what happened/ });
      // After the person's panel, in the same scrolling column.
      expect(person.firstElementChild?.firstElementChild?.compareDocumentPosition(prompt)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
      press('3');
      expect(await screen.findByText('Logged: nothing yet from Owen Brandt.')).toBeInTheDocument();
    });

    it('shows on a weekend, when there is no plan', async () => {
      const client = new FixtureClient(buildFixture('weekend'));
      renderWithApp(<TodayPage />, { client });
      expect(await screen.findByRole('region', { name: /Last week, what happened/ })).toBeInTheDocument();
    });
  });
});
