import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { focusManager } from '@tanstack/react-query';
import { useLocation } from 'react-router';
import type { OutboxItem } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture, companyId, ids, messageOf, type FixtureDb, type Scenario } from '@/api/fixture/data';
import { renderWithApp } from '@/test/render';
import OutboxPage from './index';

function Where() {
  const location = useLocation();
  return <p data-testid="where">{location.pathname}</p>;
}

async function outbox(opts: { route?: string; scenario?: Scenario; db?: FixtureDb } = {}) {
  const db = opts.db ?? buildFixture(opts.scenario ?? 'default');
  const client = new FixtureClient(db);
  const view = renderWithApp(
    <>
      <OutboxPage />
      <Where />
    </>,
    { route: opts.route ?? '/outbox', client },
  );
  await screen.findByRole('group', { name: 'Sender' });
  return { ...view, client, db };
}

const press = (key: string, init: KeyboardEventInit = {}) => fireEvent.keyDown(document.body, { key, ...init });
const strip = (name: string) => within(screen.getByRole('group', { name }));
// Role queries with a name regex walk every row's accessible name, which is slow over 90 rows; the name is in a <b>.
const findRow = (name: string) => screen.queryByText(name, { selector: 'b' })?.closest<HTMLElement>('[role="option"]') ?? null;
const row = (name: string) => {
  const found = findRow(name);
  if (!found) throw new Error(`No row for ${name}`);
  return found;
};
const groupOf = (name: string) => row(name).closest('[data-group]')?.getAttribute('data-group');
const headings = () =>
  [...document.querySelectorAll<HTMLElement>('button[data-group-head]')].map((b) => b.querySelector('[data-label]')?.textContent);

/** A poll or a refocus: every active query refetches. */
async function refetch() {
  await act(async () => {
    focusManager.setFocused(false);
    focusManager.setFocused(true);
  });
}

afterEach(() => {
  focusManager.setFocused(undefined);
  window.localStorage.clear();
});

describe('SendingStrip', () => {
  it('shows the sender, the day so far, the limit and the next send', async () => {
    await outbox();
    expect(strip('Sender').getByText('Running')).toBeInTheDocument();
    expect(strip('Sender').getByText('Weekdays 08:30 to 16:30, their time')).toBeInTheDocument();
    expect(strip('Sent today').getByText('20')).toBeInTheDocument();
    expect(strip('Sent today').getByText('of 30')).toBeInTheDocument();
    expect(strip('Sent today').getByRole('img', { name: 'Limit level 2 of 9: 30 a day. Today 20 sent.' })).toBeInTheDocument();
    expect(strip('Limit').getByText('Level 2 of 9, 30 a day')).toBeInTheDocument();
    expect(strip('Limit').getByText('40 tomorrow if today stays clean')).toBeInTheDocument();
    expect(strip('Next send').getByText('10:42 ET')).toBeInTheDocument();
    expect(strip('Next send').getByText('Jordan Price, Mintlify')).toBeInTheDocument();
  });

  it('Pause sending shows Paused by you and Resume sending', async () => {
    const { client } = await outbox();
    const pause = vi.spyOn(client, 'pauseSending');
    fireEvent.click(screen.getByRole('button', { name: 'Pause sending' }));
    expect(strip('Sender').getByText('Paused by you')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Resume sending' })).toBeInTheDocument();
    expect(pause).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(strip('Next send').getByText('Sending is paused until you resume.')).toBeInTheDocument());
  });

  it('a hard block turns the Sender cell red with Gmail\'s message, and Resume asks first', async () => {
    const { client, db } = await outbox({ scenario: 'hard_block' });
    const message = db.outbox.sending.gmailMessage ?? '';
    const sender = screen.getByRole('group', { name: 'Sender' });
    expect(sender).toHaveAttribute('data-tone', 'bad');
    expect(within(sender).getByText('Paused: hard block')).toBeInTheDocument();
    expect(within(sender).getByText(message)).toBeInTheDocument();
    expect(screen.getByText('Sending paused: Gmail said the daily limit was reached.')).toBeInTheDocument();

    const resume = vi.spyOn(client, 'resumeSending');
    fireEvent.click(screen.getByRole('button', { name: 'Resume sending' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(within(dialog).getByText(message)).toBeInTheDocument();
    expect(resume).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Resume sending' }));
    await waitFor(() => expect(resume).toHaveBeenCalledTimes(1));
    expect(resume.mock.calls[0]?.[0]).toBe(true);
    await waitFor(() => expect(strip('Sender').getByText('Running')).toBeInTheDocument());
  });

  it('signed out: a red Sender cell and an alert above the list that names the fix', async () => {
    await outbox({ scenario: 'signed_out' });
    expect(screen.getByRole('group', { name: 'Sender' })).toHaveAttribute('data-tone', 'bad');
    expect(strip('Sender').getByText('Gmail signed out')).toBeInTheDocument();
    const alert = screen.getByRole('alert');
    expect(within(alert).getByText('Gmail is signed out')).toBeInTheDocument();
    expect(within(alert).getByText(/Sign in again with mail\/gmail\.py auth in the live checkout\./)).toBeInTheDocument();
    expect(within(alert).getByText(/Approve still works/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Resume sending/ })).toBeNull();
  });
});

describe('OutboxList', () => {
  it('lists the groups in spec order, Closed collapsed with its breakdown', async () => {
    await outbox();
    expect(headings()).toEqual(['Replied', 'Drafts to review', 'Approved', 'Sending', 'Checking whether it went out', 'Sent', 'Paused', 'Closed']);
    const closed = document.querySelector('button[data-group-head="closed"]');
    expect(closed).toHaveAttribute('aria-expanded', 'false');
    expect(closed).toHaveTextContent('17');
    expect(closed).toHaveTextContent('bounced 1, skipped 4, company closed 12');
    expect(findRow('Rosa Diaz')).toBeNull();

    expect(groupOf('Priya Natarajan')).toBe('replied');
    expect(within(row('Priya Natarajan')).getByText('Holding 2 queued emails at Browserbase')).toBeInTheDocument();
    expect(within(row('Kenji Watanabe')).getByText('Blocked')).toBeInTheDocument();
    expect(within(row('Dana Whitcomb')).getByText('1 warning')).toBeInTheDocument();
    expect(within(row('Marcus Feld')).getByText('Follow-up 2 of 3')).toBeInTheDocument();
    expect(within(row('Omar Haddad')).getByText('Draft')).toBeInTheDocument();
    expect(within(row('Jordan Price')).getByText('10:42 ET')).toBeInTheDocument();
    expect(within(row('Arjun Mehta')).getByText('after 11:05 ET')).toBeInTheDocument();
    expect(within(row('Felix Graham')).getByText('tomorrow')).toBeInTheDocument();
    expect(within(row('Nina Kovac')).getByText('Follow-up 1 due Fri Nov 13')).toBeInTheDocument();
    expect(within(row('Theo Brandt')).getByText('Nov 5')).toBeInTheDocument();

    fireEvent.click(closed as Element);
    expect(closed).toHaveAttribute('aria-expanded', 'true');
    expect(row('Rosa Diaz')).toBeInTheDocument();
  });

  it('j and k move the selection, shift+j and shift+k move between groups', async () => {
    await outbox();
    const where = () => screen.getByTestId('where').textContent;
    press('j');
    expect(where()).toBe(`/outbox/${ids.touch.priya}`);
    expect(row('Priya Natarajan')).toHaveAttribute('aria-selected', 'true');
    press('j');
    expect(where()).toBe(`/outbox/${ids.touch.kenji}`);
    press('J', { shiftKey: true });
    expect(where()).toBe(`/outbox/${ids.touch.jordan}`);
    press('K', { shiftKey: true });
    expect(where()).toBe(`/outbox/${ids.touch.kenji}`);
    press('k');
    expect(where()).toBe(`/outbox/${ids.touch.priya}`);
    press('k');
    expect(where()).toBe(`/outbox/${ids.touch.priya}`);
  });

  it('selected row stays put after it is approved', async () => {
    const { client } = await outbox({ route: `/outbox/${ids.touch.omar}` });
    expect(row('Omar Haddad')).toHaveAttribute('aria-selected', 'true');
    await client.approve(messageOf(ids.touch.omar), 1, { requestId: 'from-another-tab' });
    await refetch();
    await waitFor(() => expect(within(row('Omar Haddad')).getByText('Approved')).toBeInTheDocument());
    expect(groupOf('Omar Haddad')).toBe('review');
    expect(row('Omar Haddad')).toHaveAttribute('aria-selected', 'true');

    press('j');
    expect(row('Sara Lindqvist')).toHaveAttribute('aria-selected', 'true');
    expect(groupOf('Omar Haddad')).toBe('approved');
  });

  it('a refetch that adds drafts shows a 2 new pill', async () => {
    const db = buildFixture();
    await outbox({ db, route: `/outbox/${ids.touch.kenji}` });
    const kenji = db.outbox.items.find((i) => i.touchId === ids.touch.kenji) as OutboxItem;
    const fresh = (touchId: number, name: string, sortKey: string): OutboxItem => ({
      ...structuredClone(kenji), touchId, messageId: touchId + 3000, sortKey, person: { ...kenji.person, id: touchId, name },
    });
    db.outbox.items.push(fresh(9901, 'Imogen Achterberg', '1.00000'), fresh(9902, 'Lazlo Brightwater', '1.000001'));
    db.outbox.counts.review += 2;

    const before = [...document.querySelectorAll('[role="option"]')].map((o) => o.getAttribute('data-touch'));
    await refetch();
    const pill = await screen.findByRole('button', { name: '2 new' });
    expect(pill.closest('[data-group]')).toHaveAttribute('data-group', 'review');
    expect(findRow('Imogen Achterberg')).toBeNull();
    expect([...document.querySelectorAll('[role="option"]')].map((o) => o.getAttribute('data-touch'))).toEqual(before);

    fireEvent.click(pill);
    expect(row('Imogen Achterberg')).toBeInTheDocument();
    expect(row('Lazlo Brightwater')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '2 new' })).toBeNull();
    expect(row('Kenji Watanabe')).toHaveAttribute('aria-selected', 'true');
  });

  it('shift+j reveals new rows too', async () => {
    const db = buildFixture();
    await outbox({ db, route: `/outbox/${ids.touch.kenji}` });
    const kenji = db.outbox.items.find((i) => i.touchId === ids.touch.kenji) as OutboxItem;
    db.outbox.items.push({ ...structuredClone(kenji), touchId: 9903, messageId: 12903, sortKey: '1.00000', person: { ...kenji.person, id: 9903, name: 'Odile Ferncastle' } });
    await refetch();
    await screen.findByRole('button', { name: '1 new' });
    press('J', { shiftKey: true });
    expect(row('Odile Ferncastle')).toBeInTheDocument();
    expect(row('Kenji Watanabe')).toHaveAttribute('aria-selected', 'true');
  });

  it('the empty Outbox says what to do next', async () => {
    await outbox({ scenario: 'empty' });
    expect(screen.getByText('No drafts to review.')).toBeInTheDocument();
    expect(screen.getByText("Paste people for a picked company in the side terminal. Claude's drafts show up here.")).toBeInTheDocument();
  });
});

describe('Outbox filtered to a company', () => {
  it('?company= shows only that company, with a way to clear it', async () => {
    await outbox({ route: `/outbox?company=${companyId('Mintlify')}` });
    expect(screen.getByText('Showing only Mintlify')).toBeInTheDocument();
    expect(row('Dana Whitcomb')).toBeInTheDocument();
    expect(row('Jordan Price')).toBeInTheDocument();
    expect(findRow('Kenji Watanabe')).toBeNull();
    expect(findRow('Priya Natarajan')).toBeNull();
    expect(headings()).not.toContain('Replied');
    // The group counts follow the filter.
    expect(document.querySelector('button[data-group-head="review"]')).toHaveTextContent('2');

    fireEvent.click(screen.getByRole('button', { name: 'Show all companies' }));
    expect(screen.queryByText('Showing only Mintlify')).toBeNull();
    expect(row('Kenji Watanabe')).toBeInTheDocument();
    expect(row('Priya Natarajan')).toBeInTheDocument();
  });

  it('a company with nothing in the Outbox says so instead of showing the empty Outbox', async () => {
    await outbox({ route: `/outbox?company=${companyId('Modal')}` });
    expect(screen.getByText('Nothing in the Outbox for this company')).toBeInTheDocument();
    expect(screen.queryByText('No drafts to review.')).toBeNull();
    fireEvent.click(screen.getAllByRole('button', { name: 'Show all companies' })[0] as HTMLElement);
    expect(row('Kenji Watanabe')).toBeInTheDocument();
  });
});
