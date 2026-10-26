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
  /** The strip is one line; the rest lives in a popover behind Details. */
  const openDetails = async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Sending details' }));
    return screen.findByRole('dialog', { name: 'Sending details' });
  };

  it('is one line: the sender, the day so far and the next send', async () => {
    await outbox();
    const line = within(screen.getByRole('region', { name: 'Sending today' }));
    expect(line.getByText('Running')).toBeInTheDocument();
    expect(line.getByText('20 of 30 sent')).toBeInTheDocument();
    expect(line.getByText('Next 10:42 ET, Mintlify')).toBeInTheDocument();
    // The ramp, the limit and the sub-lines wait behind Details.
    expect(screen.queryByText('Level 2 of 9, 30 a day')).toBeNull();
    expect(screen.queryByRole('group', { name: 'Limit' })).toBeNull();
  });

  it('Details shows the sender window, the day, the limit, the ramp and the next send', async () => {
    await outbox();
    const panel = within(await openDetails());
    expect(panel.getByText('Weekdays 08:30 to 16:30, their time')).toBeInTheDocument();
    expect(panel.getByText('20')).toBeInTheDocument();
    expect(panel.getByText('of 30')).toBeInTheDocument();
    expect(panel.getByRole('img', { name: 'Limit level 2 of 9: 30 a day. Today 20 sent.' })).toBeInTheDocument();
    expect(panel.getByText('Level 2 of 9, 30 a day')).toBeInTheDocument();
    expect(panel.getByText('40 tomorrow if today stays clean')).toBeInTheDocument();
    expect(panel.getByText('10:42 ET')).toBeInTheDocument();
    expect(panel.getByText('Jordan Price, Mintlify')).toBeInTheDocument();
  });

  it('Pause sending is one click, outside the popover, and acts at once', async () => {
    const { client } = await outbox();
    const pause = vi.spyOn(client, 'pauseSending');
    fireEvent.click(screen.getByRole('button', { name: 'Pause sending' }));
    expect(screen.getByRole('group', { name: 'Sender' })).toHaveTextContent('Paused by you');
    expect(await screen.findByRole('button', { name: 'Resume sending' })).toBeInTheDocument();
    expect(pause).toHaveBeenCalledTimes(1);
    expect(within(screen.getByRole('region', { name: 'Sending today' })).getByText('Sending is paused until you resume.')).toBeInTheDocument();
  });

  it('a hard block turns the line red with Gmail\'s message, and Resume asks first', async () => {
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
    await waitFor(() => expect(screen.getByRole('group', { name: 'Sender' })).toHaveTextContent('Running'));
  });

  it('signed out: a red line and an alert above the list that names the fix', async () => {
    await outbox({ scenario: 'signed_out' });
    expect(screen.getByRole('group', { name: 'Sender' })).toHaveAttribute('data-tone', 'bad');
    expect(screen.getByRole('group', { name: 'Sender' })).toHaveTextContent('Gmail signed out');
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
    // Arriving opened the reply; ready drafts follow it, blocked ones come last in the same group.
    await waitFor(() => expect(where()).toBe(`/outbox/${ids.touch.priya}`));
    press('j');
    expect(where()).toBe(`/outbox/${ids.touch.dana}`);
    press('j');
    expect(where()).toBe(`/outbox/${ids.touch.marcus}`);
    press('J', { shiftKey: true });
    expect(where()).toBe(`/outbox/${ids.touch.jordan}`);
    press('K', { shiftKey: true });
    expect(where()).toBe(`/outbox/${ids.touch.dana}`);
    press('k');
    expect(where()).toBe(`/outbox/${ids.touch.priya}`);
    press('k');
    expect(where()).toBe(`/outbox/${ids.touch.priya}`);
  });

  it('j and k move focus with the selection', async () => {
    await outbox();
    await waitFor(() => expect(row('Priya Natarajan')).toHaveAttribute('aria-selected', 'true'));
    press('j');
    expect(row('Dana Whitcomb')).toHaveFocus();
    press('j');
    expect(row('Marcus Feld')).toHaveFocus();
    press('k');
    expect(row('Dana Whitcomb')).toHaveFocus();
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

  it('arriving with nothing selected opens the first reply', async () => {
    await outbox();
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(`/outbox/${ids.touch.priya}`));
    expect(row('Priya Natarajan')).toHaveAttribute('aria-selected', 'true');
    expect(await screen.findByText(/Browserbase is on hold/)).toBeInTheDocument();
  });

  it('with no reply, arriving opens the first draft he can approve, never a blocked one and never approves it', async () => {
    const db = buildFixture();
    db.outbox.items = db.outbox.items.filter((i) => i.group !== 'replied');
    const { client } = await outbox({ db });
    const approve = vi.spyOn(client, 'approve');
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(`/outbox/${ids.touch.dana}`));
    expect(row('Dana Whitcomb')).toHaveAttribute('aria-selected', 'true');
    expect(approve).not.toHaveBeenCalled();
    expect(within(row('Dana Whitcomb')).getByText('1 warning')).toBeInTheDocument();
  });

  it('with only blocked drafts, nothing is preselected', async () => {
    const db = buildFixture();
    db.outbox.items = db.outbox.items.filter((i) => i.group !== 'replied' && !(i.group === 'review' && i.blockCount === 0));
    await outbox({ db });
    expect(screen.getByText('No email selected')).toBeInTheDocument();
    expect(screen.getByTestId('where')).toHaveTextContent(/^\/outbox$/);
  });

  it('an email he opened by its address is left alone on arrival', async () => {
    await outbox({ route: `/outbox/${ids.touch.omar}` });
    expect(row('Omar Haddad')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('where')).toHaveTextContent(`/outbox/${ids.touch.omar}`);
  });

  it('blocked drafts sit under "Blocked, fix first (n)" after the ready ones, still in Drafts to review, with a Fix cue', async () => {
    await outbox();
    const review = document.querySelector('[data-group="review"]') as HTMLElement;
    const names = [...review.querySelectorAll('[role="option"] b')].map((b) => b.textContent);
    expect(names).toEqual(['Dana Whitcomb', 'Marcus Feld', 'Omar Haddad', 'Sara Lindqvist', 'Ava Moreno', 'Kenji Watanabe', 'Rafael Ortiz']);
    const subhead = within(review).getByText('Blocked, fix first (2)');
    const blockedList = within(review).getByRole('listbox', { name: 'Blocked, fix first' });
    expect(subhead.compareDocumentPosition(blockedList) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(blockedList).getAllByRole('option')).toHaveLength(2);
    expect(within(row('Kenji Watanabe')).getByText('Fix')).toBeInTheDocument();
    expect(within(row('Omar Haddad')).queryByText('Fix')).toBeNull();
    // Nothing is hidden: the count on the group head still counts them.
    expect(document.querySelector('button[data-group-head="review"]')).toHaveTextContent('7');
    expect(document.querySelector('button[data-group-head="review"]')).not.toHaveTextContent('blocked');
    // Opening a blocked row opens the email with its Blocking card.
    fireEvent.click(row('Kenji Watanabe'));
    expect(await screen.findByRole('button', { name: 'Compare drafts' })).toBeInTheDocument();
  });

  it('a blocked row that is selected keeps its place when its checks pass', async () => {
    const db = buildFixture();
    await outbox({ db, route: `/outbox/${ids.touch.kenji}` });
    const kenji = db.outbox.items.find((i) => i.touchId === ids.touch.kenji) as OutboxItem;
    kenji.blockCount = 0;
    kenji.worst = 'pass';
    await refetch();
    await waitFor(() => expect(within(row('Kenji Watanabe')).queryByText('Blocked')).toBeNull());
    const review = document.querySelector('[data-group="review"]') as HTMLElement;
    expect(within(review).getByText('Blocked, fix first (2)')).toBeInTheDocument();
    expect(within(review).getByRole('listbox', { name: 'Blocked, fix first' })).toContainElement(row('Kenji Watanabe'));
    press('j');
    expect(within(review).getByText('Blocked, fix first (1)')).toBeInTheDocument();
    expect(within(review).queryByRole('listbox', { name: 'Blocked, fix first' })?.contains(row('Kenji Watanabe'))).toBe(false);
  });

  it('approve, skip and withdraw each move the selection to the next row, and never approve it', async () => {
    const { client } = await outbox({ route: `/outbox/${ids.touch.omar}` });
    const approve = vi.spyOn(client, 'approve');
    await screen.findByRole('button', { name: 'Approve' });
    press('a');
    expect(row('Sara Lindqvist')).toHaveAttribute('aria-selected', 'true');
    expect(row('Sara Lindqvist')).toHaveFocus();
    await screen.findByRole('heading', { level: 2, name: 'Sara Lindqvist' });
    press('s');
    // Sara is skipped; Ava is next. Nothing was approved but Omar.
    expect(row('Ava Moreno')).toHaveAttribute('aria-selected', 'true');
    expect(approve).not.toHaveBeenCalledWith(messageOf(ids.touch.ava), expect.anything(), expect.anything());
    fireEvent.click(row('Jordan Price'));
    await screen.findByRole('button', { name: /Withdraw approval/ });
    press('u');
    await waitFor(() => expect(row('Arjun Mehta')).toHaveAttribute('aria-selected', 'true'));
  });

  it('refusing an approve (a blocked draft) does not move the selection', async () => {
    await outbox({ route: `/outbox/${ids.touch.kenji}` });
    await screen.findByRole('button', { name: 'Approve' });
    press('a');
    expect(row('Kenji Watanabe')).toHaveAttribute('aria-selected', 'true');
  });

  it('the last draft in a group has no next one: the selection stays on it', async () => {
    await outbox({ route: `/outbox/${ids.touch.rafael}` });
    await screen.findByRole('button', { name: 'Skip' });
    press('s');
    // Rafael was the last blocked draft; the one before it is Kenji.
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
