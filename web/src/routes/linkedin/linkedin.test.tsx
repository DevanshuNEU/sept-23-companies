import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture } from '@/api/fixture/data';
import { renderWithApp } from '@/test/render';
import LinkedInPage from './index';

const press = (key: string) => fireEvent.keyDown(document.body, { key });
const tab = (name: RegExp) => screen.getByRole('tab', { name });
// The live region also says the selected name, so rows are looked up inside the table (hidden while a dialog is open).
const table = () => screen.getByRole('table', { hidden: true });
const row = (name: string) => {
  const tr = within(table()).getByText(name, { exact: true }).closest('tr');
  if (!tr) throw new Error(`no row for ${name}`);
  return tr;
};
const queryRow = (name: string) => within(table()).queryByText(name, { exact: true })?.closest('tr') ?? null;
const pagehide = () => fireEvent(window, new Event('pagehide'));

async function setup(client = new FixtureClient()) {
  const view = renderWithApp(<LinkedInPage />, { route: '/linkedin', client });
  await screen.findByText('Hannah Cole');
  const spy = vi.spyOn(client, 'logLinkedIn');
  const data = await client.linkedin();
  const idOf = (name: string) => {
    const p = data.groups.flatMap((g) => g.people).find((x) => x.person.name === name);
    if (!p) throw new Error(`no person ${name}`);
    return p.person.id;
  };
  return { ...view, client, spy, idOf };
}

describe('LinkedIn list', () => {
  it('shows the four tabs with their counts and requests this week', async () => {
    await setup();
    expect(screen.getByRole('heading', { level: 1, name: 'LinkedIn list' })).toBeInTheDocument();
    expect(tab(/^To reach/)).toHaveTextContent('12');
    expect(tab(/^Waiting/)).toHaveTextContent('9');
    expect(tab(/^Replied/)).toHaveTextContent('2');
    expect(tab(/^All/)).toHaveTextContent('31');
    expect(tab(/^To reach/)).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('requests this week').parentElement).toHaveTextContent('14 requests this week');
  });

  it('shows the Mintlify header with the role and "8 emailed, 4 here"', async () => {
    await setup();
    const header = screen.getByRole('link', { name: 'Mintlify' }).closest('tr');
    if (!header) throw new Error('no Mintlify header');
    expect(header).toHaveTextContent('Role to name');
    expect(header).toHaveTextContent('Software Engineer, New Grad');
    expect(header).toHaveTextContent('8 emailed, 4 here');
  });

  it('shows why LinkedIn, the kind and the next step on each row', async () => {
    await setup();
    const hannah = row('Hannah Cole');
    expect(hannah).toHaveTextContent('Engineer');
    expect(hannah).toHaveTextContent('Past the 8 emailed');
    expect(hannah).toHaveTextContent('Not started');
    expect(within(hannah).getByRole('button', { name: /Log request/ })).toBeInTheDocument();
    expect(row('Ethan Wu')).toHaveTextContent('No email found');
  });

  it('r on Hannah Cole logs request_sent with Undo and her row moves to Waiting only after the selection moves', async () => {
    const { spy, idOf } = await setup();
    fireEvent.click(row('Hannah Cole'));
    press('r');

    expect(await screen.findByText('Logged: request sent to Hannah Cole.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();
    // Counts move at once; the selected row stays where it was.
    expect(tab(/^To reach/)).toHaveTextContent('11');
    expect(tab(/^Waiting/)).toHaveTextContent('10');
    expect(row('Hannah Cole')).toHaveTextContent('Requested today');
    expect(row('Hannah Cole')).toHaveAttribute('aria-selected', 'true');
    expect(spy).not.toHaveBeenCalled();

    press('j');
    await waitFor(() => expect(queryRow('Hannah Cole')).toBeNull());
    expect(row('Owen Clarke')).toHaveAttribute('aria-selected', 'true');

    press('2');
    await waitFor(() => expect(queryRow('Hannah Cole')).not.toBeNull());
    expect(row('Hannah Cole')).toHaveTextContent('Requested today');

    pagehide();
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy).toHaveBeenCalledWith(idOf('Hannah Cole'), 'request_sent', expect.objectContaining({ requestId: expect.any(String) }));
  });

  it('Undo puts Hannah back at Not started and logs nothing', async () => {
    const { spy } = await setup();
    fireEvent.click(row('Hannah Cole'));
    press('r');
    fireEvent.click(await screen.findByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(row('Hannah Cole')).toHaveTextContent('Not started'));
    expect(tab(/^To reach/)).toHaveTextContent('12');
    pagehide();
    await new Promise((r) => setTimeout(r, 20));
    expect(spy).not.toHaveBeenCalled();
  });

  it('a held r logs once', async () => {
    const { spy } = await setup();
    fireEvent.click(row('Hannah Cole'));
    press('r');
    press('r');
    await screen.findByText('Logged: request sent to Hannah Cole.');
    pagehide();
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 20));
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('r, a, m and y log only the next step: a step already logged says so, a later one is not logged', async () => {
    const { spy } = await setup();
    // Toasts from earlier tests can still be on screen, so count against what is there now.
    const logged = () => screen.queryAllByText(/^Logged:/).length;
    const undos = () => screen.queryAllByRole('button', { name: 'Undo' }).length;
    const [loggedBefore, undosBefore] = [logged(), undos()];
    // Hannah Cole has nothing logged: only the request is next.
    fireEvent.click(row('Hannah Cole'));
    for (const key of ['a', 'm']) press(key);
    expect(await screen.findByText('Next for Hannah Cole: log request (R).')).toBeInTheDocument();
    press('y');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect([logged(), undos()]).toEqual([loggedBefore, undosBefore]);
    expect(row('Hannah Cole')).toHaveTextContent('Not started');

    // Grace Kim asked and was accepted: r and a are behind her, y is a step ahead.
    press('2');
    fireEvent.click(await screen.findByText('Grace Kim'));
    press('r');
    expect(await screen.findByText('Already logged: request sent')).toBeInTheDocument();
    press('a');
    expect(await screen.findByText('Already logged: accepted')).toBeInTheDocument();
    press('y');
    expect(await screen.findByText('Next for Grace Kim: log message (M).')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect([logged(), undos()]).toEqual([loggedBefore, undosBefore]);

    pagehide();
    await new Promise((r) => setTimeout(r, 20));
    expect(spy).not.toHaveBeenCalled();

    press('m');
    expect(await screen.findByText('Logged: message sent to Grace Kim.')).toBeInTheDocument();
    pagehide();
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy.mock.calls[0]?.[1]).toBe('message_sent');
  });

  it('y on a person who already replied says so and opens no dialog', async () => {
    const { spy } = await setup();
    press('3');
    fireEvent.click(await screen.findByText('Anneke Visser'));
    press('y');
    expect(await screen.findByText('Already logged: reply')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(spy).not.toHaveBeenCalled();
  });

  it('y asks for the reply kind before logging', async () => {
    const { spy, idOf } = await setup();
    press('2');
    fireEvent.click(await screen.findByText('Ibrahim Sall'));
    press('y');

    const dialog = await screen.findByRole('dialog', { name: 'What kind of reply?' });
    for (const name of ['Interested', 'Not now', 'Said no', 'Asked to stop']) {
      expect(within(dialog).getByRole('button', { name })).toBeInTheDocument();
    }
    expect(row('Ibrahim Sall')).toHaveTextContent('Messaged Mon Nov 9');
    expect(screen.queryByText(/^Logged: reply/)).toBeNull();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Interested' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(await screen.findByText('Logged: reply from Ibrahim Sall (Interested).')).toBeInTheDocument();
    expect(row('Ibrahim Sall')).toHaveTextContent('Replied today');
    expect(tab(/^Replied/)).toHaveTextContent('3');

    pagehide();
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy).toHaveBeenCalledWith(idOf('Ibrahim Sall'), 'replied', expect.objectContaining({ requestId: expect.any(String) }), 'interested');
  });

  it('Said no logs at once, without Undo', async () => {
    const { spy, idOf } = await setup();
    press('2');
    fireEvent.click(await screen.findByText('Ibrahim Sall'));
    press('y');
    const dialog = await screen.findByRole('dialog', { name: 'What kind of reply?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Said no' }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy).toHaveBeenCalledWith(idOf('Ibrahim Sall'), 'replied', expect.objectContaining({ requestId: expect.any(String) }), 'said_no');
    expect(await screen.findByText('Logged: reply from Ibrahim Sall (Said no).')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
  });

  it('Browserbase is held by Priya and its rows ask "Wait for Priya?"', async () => {
    await setup();
    const header = screen.getByRole('link', { name: 'Browserbase' }).closest('tr');
    if (!header) throw new Error('no Browserbase header');
    expect(header).toHaveTextContent('Held: Priya replied by email');
    expect(row('Owen Clarke')).toHaveTextContent('Wait for Priya?');
    expect(row('Hannah Cole')).not.toHaveTextContent('Wait for');

    press('4');
    expect(await screen.findByText('Maya Ellison')).toBeInTheDocument();
    expect(row('Maya Ellison')).toHaveTextContent('Wait for Priya?');
  });

  it('builds the hold words from heldBy.personName', async () => {
    const db = buildFixture();
    const group = db.linkedin.groups.find((g) => g.company.name === 'Browserbase');
    if (!group?.heldBy) throw new Error('fixture lost the Browserbase hold');
    group.heldBy = { ...group.heldBy, personName: 'Asha Varma' };
    await setup(new FixtureClient(db));
    expect(row('Owen Clarke')).toHaveTextContent('Wait for Asha?');
    expect(screen.getByText('Held: Asha replied by email')).toBeInTheDocument();
  });

  it('1 to 4 switch tabs', async () => {
    await setup();
    press('3');
    await waitFor(() => expect(tab(/^Replied/)).toHaveAttribute('aria-selected', 'true'));
    expect(screen.getByText('Mira Shah')).toBeInTheDocument();
    expect(queryRow('Hannah Cole')).toBeNull();
    press('2');
    await waitFor(() => expect(tab(/^Waiting/)).toHaveAttribute('aria-selected', 'true'));
    expect(screen.getByText('Luis Romero')).toBeInTheDocument();
    press('4');
    await waitFor(() => expect(tab(/^All/)).toHaveAttribute('aria-selected', 'true'));
    expect(screen.getByText('Hannah Cole')).toBeInTheDocument();
    expect(screen.getByText('Mira Shah')).toBeInTheDocument();
    press('1');
    await waitFor(() => expect(tab(/^To reach/)).toHaveAttribute('aria-selected', 'true'));
  });

  it('c copies the role title for the selected person and l opens their profile', async () => {
    const user = userEvent.setup();
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    await setup();
    await user.click(screen.getByText('Ethan Wu'));
    press('c');
    expect(await screen.findByText('Copied: Software Engineer, New Grad')).toBeInTheDocument();
    await expect(navigator.clipboard.readText()).resolves.toBe('Software Engineer, New Grad');

    press('l');
    expect(open).toHaveBeenCalledWith('https://linkedin.example/in/ethan-wu', '_blank', 'noopener,noreferrer');
    open.mockRestore();
  });

  it('l opens nothing when the saved profile address is not a web address', async () => {
    class Unsafe extends FixtureClient {
      override async linkedin() {
        const v = await super.linkedin();
        return {
          ...v,
          groups: v.groups.map((g) => ({
            ...g,
            people: g.people.map((p) => (p.person.name === 'Ethan Wu' ? { ...p, person: { ...p.person, linkedinUrl: 'javascript:alert(1)' } } : p)),
          })),
        };
      }
    }
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    await setup(new Unsafe());
    fireEvent.click(screen.getByText('Ethan Wu'));
    press('l');
    expect(await screen.findByText('No LinkedIn profile saved for Ethan Wu.')).toBeInTheDocument();
    expect(open).not.toHaveBeenCalled();
    expect(document.querySelector('a[href^="javascript:"]')).toBeNull();
    open.mockRestore();
  });

  it('filters people or companies', async () => {
    await setup();
    fireEvent.change(screen.getByRole('textbox', { name: 'Filter' }), { target: { value: 'graphite' } });
    await waitFor(() => expect(queryRow('Hannah Cole')).toBeNull());
    expect(screen.getByText('Percival Moon')).toBeInTheDocument();
  });

  it('says so when a tab is empty', async () => {
    const db = buildFixture();
    db.linkedin.groups.forEach((g) => g.people.forEach((p) => { if (p.tab === 'to_reach') p.tab = 'waiting'; }));
    db.linkedin.counts = { ...db.linkedin.counts, toReach: 0 };
    renderWithApp(<LinkedInPage />, { route: '/linkedin', client: new FixtureClient(db) });
    expect(await screen.findByText('Nobody to reach.')).toBeInTheDocument();
    expect(screen.getByText('People past the email budget land here when Claude adds them.')).toBeInTheDocument();
  });
});
