import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture, companyId, ids, messageOf } from '@/api/fixture/data';
import { usePendingWrite } from '@/pending/pending';
import { useLocation } from 'react-router';
import { AppShell } from '@/shell/app-shell';
import { renderWithApp } from '@/test/render';
import OutboxPage from './index';

const T = ids.touch;
const BLOCKED = "Blocked: near-identical to Ava Moreno's draft. Edit, then approve.";
const SAVE_FIRST = 'Save your edits first (Cmd S)';

/** Opens the Outbox on one email and waits for its detail. `shell` adds the sidebar, for the counts. */
async function open(touchId: number, name: string, opts: { shell?: boolean; scenario?: Parameters<typeof buildFixture>[0] } = {}) {
  const client = new FixtureClient(buildFixture(opts.scenario ?? 'default'));
  const view = renderWithApp(
    <>
      {opts.shell ? <AppShell /> : null}
      <OutboxPage />
    </>,
    { route: `/outbox/${touchId}`, client },
  );
  await screen.findByRole('heading', { level: 2, name });
  return { ...view, client };
}

const press = (key: string, init: KeyboardEventInit = {}, target: Element = document.body) => fireEvent.keyDown(target, { key, ...init });
const findRow = (name: string) => screen.queryByText(name, { selector: 'b' })?.closest<HTMLElement>('[role="option"]') ?? null;
const row = (name: string) => {
  const found = findRow(name);
  if (!found) throw new Error(`No row for ${name}`);
  return found;
};
const approveButton = () => screen.getByRole('button', { name: 'Approve' });
const bodyBox = () => screen.getByRole('textbox', { name: 'Email body' });
/** Focus in the body, then real key presses. (A pointer click is skipped: jsdom has no layout, so the split handle would take it.) */
async function typeInBody(text: string) {
  act(() => {
    const box = bodyBox() as HTMLTextAreaElement;
    box.focus();
    box.setSelectionRange(box.value.length, box.value.length);
  });
  await userEvent.setup().keyboard(text);
}
/** Undo buttons in toasts that are not on their way out (a dismissed toast lingers for its exit animation). */
const liveUndos = () => screen.queryAllByRole('button', { name: 'Undo' }).filter((b) => b.closest('[data-sonner-toast]')?.getAttribute('data-removed') !== 'true');
const toastText = () => [...document.querySelectorAll('[data-sonner-toast]')].map((t) => t.textContent ?? '').join('|');
const checks = () => within(screen.getByRole('group', { name: 'Check results' }));
const sidebarOutbox = () => within(document.querySelector<HTMLElement>('[data-slot="sidebar"]') ?? document.body).getByRole('link', { name: /^Outbox/ });

// Fake timers turn a 0 ms timeout set during a tick into 1 ms, so a little time is needed, not just a tick.
async function settle(ms = 0) {
  await act(() => vi.advanceTimersByTimeAsync(ms));
}
/** Only the undo window and Query's notifications are timed; the rest of a test runs on the real clock. */
const startClock = () => vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'requestAnimationFrame', 'cancelAnimationFrame'] });

afterEach(async () => {
  if (vi.isFakeTimers()) {
    await act(() => vi.runOnlyPendingTimersAsync());
    vi.useRealTimers();
  }
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe('approve', () => {
  it('Kenji: Approve is aria-disabled with the block in its tooltip, and neither a nor a click approves', async () => {
    const { client } = await open(T.kenji, 'Kenji Watanabe');
    const approve = vi.spyOn(client, 'approve');
    const button = approveButton();
    expect(button).toHaveAttribute('aria-disabled', 'true');
    expect(button).not.toBeDisabled();
    act(() => button.focus());
    expect(await screen.findByRole('tooltip')).toHaveTextContent(BLOCKED);

    press('a');
    await waitFor(() => expect(toastText()).toContain(BLOCKED));
    fireEvent.click(button);
    expect(approve).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
    expect(within(row('Kenji Watanabe')).getByText('Blocked')).toBeInTheDocument();
    expect(screen.getAllByText(BLOCKED, { selector: '[data-sonner-toast] *' })).toHaveLength(1);
  });

  it('Omar: a applies at once with Undo, and the write commits once after 8 seconds', async () => {
    const { client } = await open(T.omar, 'Omar Haddad', { shell: true });
    expect(sidebarOutbox()).toHaveAccessibleName('Outbox, 1 reply to handle, 7 drafts to review');
    const approve = vi.spyOn(client, 'approve');
    startClock();
    press('a');
    await settle();
    expect(within(row('Omar Haddad')).getByText('Approved')).toBeInTheDocument();
    expect(sidebarOutbox()).toHaveAccessibleName('Outbox, 1 reply to handle, 6 drafts to review');
    expect(toastText()).toContain('Approved Omar Haddad. Sends after 10:55 ET.');
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();

    await settle(7999);
    expect(approve).not.toHaveBeenCalled();
    await settle(1);
    expect(approve).toHaveBeenCalledTimes(1);
    expect(approve.mock.calls[0]?.[0]).toBe(messageOf(T.omar));
    expect(approve.mock.calls[0]?.[1]).toBe(1);
    await settle(50);
    expect(within(row('Omar Haddad')).getByText('Approved')).toBeInTheDocument();
    expect(sidebarOutbox()).toHaveAccessibleName('Outbox, 1 reply to handle, 6 drafts to review');
  });

  it('approve fires once on key repeat', async () => {
    const { client } = await open(T.omar, 'Omar Haddad');
    const approve = vi.spyOn(client, 'approve');
    const button = approveButton();
    startClock();
    press('a');
    press('a', { repeat: true });
    press('a', { repeat: true });
    await settle();
    // The first press already turned Approve into Withdraw approval: there is nothing left to click.
    expect(button).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
    expect(screen.getAllByRole('button', { name: 'Undo' })).toHaveLength(1);
    await settle(8000);
    expect(approve).toHaveBeenCalledTimes(1);
  });

  it('the store ignores a second approve of the same message inside the window', async () => {
    const client = new FixtureClient(buildFixture());
    const approve = vi.spyOn(client, 'approve');
    function Twice() {
      const write = usePendingWrite();
      const go = () =>
        write({
          id: `approve:${messageOf(T.omar)}`,
          label: 'Approved Omar Haddad.',
          afterCommit: 'Approved.',
          patches: [],
          commit: (o) => client.approve(messageOf(T.omar), 1, o),
        });
      return (
        <button type="button" onClick={() => { go(); go(); }}>
          approve twice
        </button>
      );
    }
    renderWithApp(<Twice />, { route: '/outbox', client });
    const button = await screen.findByRole('button', { name: 'approve twice' });
    startClock();
    fireEvent.click(button);
    fireEvent.click(button);
    await settle();
    expect(screen.getAllByRole('button', { name: 'Undo' })).toHaveLength(1);
    await settle(8000);
    expect(approve).toHaveBeenCalledTimes(1);
  });

  it('Undo inside the window drops the write and restores the draft', async () => {
    const { client } = await open(T.omar, 'Omar Haddad', { shell: true });
    const approve = vi.spyOn(client, 'approve');
    startClock();
    press('a');
    await settle();
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    await settle();
    expect(within(row('Omar Haddad')).getByText('Draft')).toBeInTheDocument();
    expect(sidebarOutbox()).toHaveAccessibleName('Outbox, 1 reply to handle, 7 drafts to review');
    await settle(9000);
    expect(approve).not.toHaveBeenCalled();
  });

  it('typing a in the body does not approve', async () => {
    const { client } = await open(T.omar, 'Omar Haddad');
    const approve = vi.spyOn(client, 'approve');
    await typeInBody('a');
    expect((bodyBox() as HTMLTextAreaElement).value.endsWith('a')).toBe(true);
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
    expect(approve).not.toHaveBeenCalled();
    expect(within(row('Omar Haddad')).getByText('Draft')).toBeInTheDocument();
  });

  it('a does nothing while the Close dialog is open', async () => {
    const { client } = await open(T.omar, 'Omar Haddad');
    const approve = vi.spyOn(client, 'approve');
    press('c');
    await screen.findByRole('dialog');
    press('a');
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
    expect(approve).not.toHaveBeenCalled();
  });

  it('unsaved edits disable Approve, and mod+enter saves, then approves', async () => {
    const { client } = await open(T.omar, 'Omar Haddad');
    const save = vi.spyOn(client, 'saveMessage');
    const approve = vi.spyOn(client, 'approve');
    await typeInBody(' Thanks again.');
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument();
    expect(approveButton()).toHaveAttribute('aria-disabled', 'true');
    act(() => approveButton().focus());
    expect(await screen.findByRole('tooltip')).toHaveTextContent(SAVE_FIRST);
    press('a');
    await waitFor(() => expect(toastText()).toContain(SAVE_FIRST));
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();

    startClock();
    press('Enter', { ctrlKey: true }, bodyBox());
    await settle();
    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls[0]?.[0]).toBe(messageOf(T.omar));
    expect(save.mock.calls[0]?.[1]).toMatchObject({ revision: 1 });
    expect((save.mock.calls[0]?.[1] as { body: string }).body.endsWith(' Thanks again.')).toBe(true);
    expect(toastText()).toContain('Approved Omar Haddad. Sends after 10:55 ET.');
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();
    expect(approve).not.toHaveBeenCalled();
    await settle(8000);
    expect(approve).toHaveBeenCalledTimes(1);
    expect(approve.mock.calls[0]?.[1]).toBe(2);
  });

  it('mod+enter does not approve a draft that is still blocked after the save', async () => {
    const { client } = await open(T.kenji, 'Kenji Watanabe');
    const approve = vi.spyOn(client, 'approve');
    await typeInBody(' P.S. Thanks.');
    press('Enter', { ctrlKey: true }, bodyBox());
    await waitFor(() => expect(toastText()).toContain(BLOCKED));
    expect(approve).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
  });

  it('u withdraws an approved email at once', async () => {
    const { client } = await open(T.jordan, 'Jordan Price');
    const unapprove = vi.spyOn(client, 'unapprove');
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
    expect(screen.queryByRole('textbox', { name: 'Email body' })).toBeNull();
    press('u');
    await waitFor(() => expect(unapprove).toHaveBeenCalledTimes(1));
    expect(unapprove.mock.calls[0]?.[0]).toBe(messageOf(T.jordan));
    await waitFor(() => expect(within(row('Jordan Price')).getByText('Draft')).toBeInTheDocument());
    expect(await screen.findByRole('button', { name: 'Approve' })).toBeInTheDocument();
  });

  it('u inside the approve window cancels the approval: nothing reaches the API', async () => {
    const { client } = await open(T.omar, 'Omar Haddad', { shell: true });
    const approve = vi.spyOn(client, 'approve');
    const unapprove = vi.spyOn(client, 'unapprove');
    startClock();
    press('a');
    await settle();
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();
    press('u');
    press('u');
    await settle(1000);
    expect(within(row('Omar Haddad')).getByText('Draft')).toBeInTheDocument();
    expect(sidebarOutbox()).toHaveAccessibleName('Outbox, 1 reply to handle, 7 drafts to review');
    expect(liveUndos()).toHaveLength(0);
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument();
    await settle(9000);
    expect(approve).not.toHaveBeenCalled();
    expect(unapprove).not.toHaveBeenCalled();
  });

  it('u leaves another email\'s pending write alone', async () => {
    const { client } = await open(T.sara, 'Sara Lindqvist');
    const approve = vi.spyOn(client, 'approve');
    startClock();
    press('a');
    await settle();
    fireEvent.click(row('Omar Haddad'));
    await settle();
    press('a');
    await settle();
    expect(screen.getAllByRole('button', { name: 'Undo' })).toHaveLength(2);
    press('u');
    await settle(1000);
    expect(within(row('Omar Haddad')).getByText('Draft')).toBeInTheDocument();
    expect(within(row('Sara Lindqvist')).getByText('Approved')).toBeInTheDocument();
    expect(liveUndos()).toHaveLength(1);
    await settle(8000);
    expect(approve).toHaveBeenCalledTimes(1);
    expect(approve.mock.calls[0]?.[0]).toBe(messageOf(T.sara));
  });
});

describe('save and conflict', () => {
  it('mod+s saves and the check list updates', async () => {
    const { client } = await open(T.kenji, 'Kenji Watanabe');
    const save = vi.spyOn(client, 'saveMessage');
    expect(checks().getByText(/Near-identical to Ava Moreno/)).toBeInTheDocument();
    const edited = (bodyBox() as HTMLTextAreaElement).value.replace('Lately I\'ve built', 'These days I build');
    fireEvent.change(bodyBox(), { target: { value: edited } });
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument();

    press('s', { ctrlKey: true }, bodyBox());
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0]?.[1]).toMatchObject({ body: edited, revision: 1 });
    await waitFor(() => expect(checks().queryByText(/Near-identical to Ava Moreno/)).toBeNull());
    expect(screen.queryByText('Unsaved changes')).toBeNull();
    expect(screen.getByText(/Saved by you/)).toBeInTheDocument();
    expect(toastText()).toContain('Saved. Checks run again.');
    expect(approveButton()).not.toHaveAttribute('aria-disabled', 'true');
  });

  it('text typed while a save is in flight stays, and the draft is still dirty', async () => {
    const { client } = await open(T.kenji, 'Kenji Watanabe');
    const original = client.saveMessage.bind(client);
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    const save = vi.spyOn(client, 'saveMessage').mockImplementation(async (...args) => {
      await gate;
      return original(...args);
    });
    const first = `${(bodyBox() as HTMLTextAreaElement).value}\nP.S. One.`;
    fireEvent.change(bodyBox(), { target: { value: first } });
    press('s', { ctrlKey: true }, bodyBox());
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    const more = `${first}\nP.S. Two, typed while saving.`;
    fireEvent.change(bodyBox(), { target: { value: more } });
    release();
    await waitFor(() => expect(toastText()).toContain('Saved. Checks run again.'));
    expect((bodyBox() as HTMLTextAreaElement).value).toBe(more);
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument();
    expect(screen.queryByText('This email changed while you were editing')).toBeNull();
    // A second save sends the extra text on top of the revision the first one made.
    press('s', { ctrlKey: true }, bodyBox());
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
    expect(save.mock.calls[1]?.[1]).toMatchObject({ body: more, revision: 2 });
    await waitFor(() => expect(screen.queryByText('Unsaved changes')).toBeNull());
    expect((bodyBox() as HTMLTextAreaElement).value).toBe(more);
  });

  async function conflicted() {
    const view = await open(T.kenji, 'Kenji Watanabe');
    const mine = `${(bodyBox() as HTMLTextAreaElement).value}\nP.S. Mine.`;
    fireEvent.change(bodyBox(), { target: { value: mine } });
    const theirs = 'Hi Kenji,\n\nA different note saved from another tab.\n\nThanks,\nDevanshu Chicholikar';
    await view.client.saveMessage(messageOf(T.kenji), { subject: 'your post on safe task retries', body: theirs, revision: 1 }, { requestId: 'other-tab' });
    press('s', { ctrlKey: true }, bodyBox());
    const alert = await screen.findByText('This email changed while you were editing');
    return { ...view, mine, theirs, alert: alert.closest<HTMLElement>('[role="alert"]') as HTMLElement };
  }

  it('a conflict keeps his text and offers Load newer or Save mine over it', async () => {
    const { mine, alert } = await conflicted();
    expect(within(alert).getByRole('button', { name: 'Load newer' })).toBeInTheDocument();
    expect(within(alert).getByRole('button', { name: 'Save mine over it' })).toBeInTheDocument();
    expect((bodyBox() as HTMLTextAreaElement).value).toBe(mine);
    expect(approveButton()).toHaveAttribute('aria-disabled', 'true');
  });

  it('Load newer replaces his text with the newer version', async () => {
    const { theirs, alert } = await conflicted();
    fireEvent.click(within(alert).getByRole('button', { name: 'Load newer' }));
    await waitFor(() => expect((bodyBox() as HTMLTextAreaElement).value).toBe(theirs));
    expect(screen.queryByText('This email changed while you were editing')).toBeNull();
  });

  it('Save mine over it saves his text on top of the newer revision', async () => {
    const { client, mine, alert } = await conflicted();
    const save = vi.spyOn(client, 'saveMessage');
    fireEvent.click(within(alert).getByRole('button', { name: 'Save mine over it' }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0]?.[1]).toMatchObject({ body: mine, revision: 2 });
    await waitFor(() => expect(screen.queryByText('This email changed while you were editing')).toBeNull());
    expect((bodyBox() as HTMLTextAreaElement).value).toBe(mine);
    expect(screen.queryByText('Unsaved changes')).toBeNull();
  });

  it('unsaved text survives moving to another email and back', async () => {
    await open(T.kenji, 'Kenji Watanabe');
    const edited = `${(bodyBox() as HTMLTextAreaElement).value}\nP.S. Draft of a thought.`;
    fireEvent.change(bodyBox(), { target: { value: edited } });
    fireEvent.click(row('Priya Natarajan'));
    await screen.findByRole('heading', { level: 2, name: 'Priya Natarajan' });
    fireEvent.click(row('Kenji Watanabe'));
    await screen.findByRole('textbox', { name: 'Email body' });
    expect((bodyBox() as HTMLTextAreaElement).value).toBe(edited);
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument();
  });
});

describe('unsaved edits outlive the Outbox', () => {
  /** The Outbox only while the URL is under /outbox, so leaving it unmounts the page like the real router does. */
  function OutboxOnly() {
    const { pathname } = useLocation();
    return pathname.startsWith('/outbox') ? <OutboxPage /> : <p>Elsewhere</p>;
  }
  const beforeUnloadPrevented = () => {
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  };

  it('an edit is still there after g t and g o', async () => {
    const client = new FixtureClient(buildFixture());
    renderWithApp(
      <>
        <AppShell />
        <OutboxOnly />
      </>,
      { route: `/outbox/${T.kenji}`, client },
    );
    await screen.findByRole('heading', { level: 2, name: 'Kenji Watanabe' });
    const edited = `${(bodyBox() as HTMLTextAreaElement).value}\nP.S. Kept across views.`;
    fireEvent.change(bodyBox(), { target: { value: edited } });

    press('g');
    press('t');
    await screen.findByText('Elsewhere');
    press('g');
    press('o');
    fireEvent.click(await waitFor(() => row('Kenji Watanabe')));
    await screen.findByRole('textbox', { name: 'Email body' });
    expect((bodyBox() as HTMLTextAreaElement).value).toBe(edited);
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument();
  });

  it('the page asks before it closes while an edit is unsaved, and stops once it is saved', async () => {
    await open(T.kenji, 'Kenji Watanabe');
    expect(beforeUnloadPrevented()).toBe(false);
    const edited = `${(bodyBox() as HTMLTextAreaElement).value}\nP.S. Not saved yet.`;
    fireEvent.change(bodyBox(), { target: { value: edited } });
    expect(beforeUnloadPrevented()).toBe(true);

    press('s', { ctrlKey: true }, bodyBox());
    await waitFor(() => expect(screen.queryByText('Unsaved changes')).toBeNull());
    expect(beforeUnloadPrevented()).toBe(false);
  });

  it('typing the text back to what is saved is not an unsaved edit', async () => {
    await open(T.kenji, 'Kenji Watanabe');
    const saved = (bodyBox() as HTMLTextAreaElement).value;
    fireEvent.change(bodyBox(), { target: { value: `${saved}x` } });
    expect(beforeUnloadPrevented()).toBe(true);
    fireEvent.change(bodyBox(), { target: { value: saved } });
    expect(beforeUnloadPrevented()).toBe(false);
  });
});

describe('external links', () => {
  it('a LinkedIn address that is not http or https is not a link', async () => {
    class Unsafe extends FixtureClient {
      override async outboxDetail(touchId: number) {
        const d = await super.outboxDetail(touchId);
        return { ...d, item: { ...d.item, person: { ...d.item.person, linkedinUrl: 'javascript:alert(1)' } } };
      }
    }
    const client = new Unsafe(buildFixture());
    renderWithApp(<OutboxPage />, { route: `/outbox/${T.kenji}`, client });
    await screen.findByRole('heading', { level: 2, name: 'Kenji Watanabe' });
    expect(screen.queryByRole('link', { name: 'LinkedIn' })).toBeNull();
    expect(document.querySelector('a[href^="javascript:"]')).toBeNull();
  });

  it('a web address stays a link', async () => {
    await open(T.kenji, 'Kenji Watanabe');
    expect(screen.getByRole('link', { name: 'LinkedIn' })).toHaveAttribute('href', expect.stringMatching(/^https:\/\//));
  });
});

describe('checks', () => {
  it('Dana: the role kind select calls setRoleKind with the person and revision', async () => {
    const { client } = await open(T.dana, 'Dana Whitcomb');
    const setRoleKind = vi.spyOn(client, 'setRoleKind');
    // Keys and a plain click: jsdom has no layout, so a pointer press would land on the split handle.
    act(() => screen.getByRole('combobox', { name: 'Role kind' }).focus());
    await userEvent.setup().keyboard('{Enter}');
    fireEvent.click(await screen.findByRole('option', { name: 'Engineer' }));
    await waitFor(() => expect(setRoleKind).toHaveBeenCalledTimes(1));
    const detail = await client.outboxDetail(T.dana);
    expect(setRoleKind.mock.calls[0]?.slice(0, 3)).toEqual([detail.item.person.id, 'engineer', 1]);
    await waitFor(() => expect(screen.queryByRole('combobox', { name: 'Role kind' })).toBeNull());
  });

  it('Compare drafts shows both drafts and how alike they are', async () => {
    await open(T.kenji, 'Kenji Watanabe');
    fireEvent.click(screen.getByRole('button', { name: 'Compare drafts' }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('92% the same')).toBeInTheDocument();
    expect(within(dialog).getByText("Kenji Watanabe's draft")).toBeInTheDocument();
    expect(within(dialog).getByText("Ava Moreno's draft")).toBeInTheDocument();
    expect(dialog.querySelectorAll('mark').length).toBeGreaterThan(0);
  });

  it('shows the role to name, the hook and the schedule', async () => {
    await open(T.kenji, 'Kenji Watanabe');
    expect(screen.getByText('Software Engineer I')).toBeInTheDocument();
    expect(screen.getByText('Posted 4 days ago, Remote US')).toBeInTheDocument();
    expect(screen.getByText('His post on retries without double work')).toBeInTheDocument();
    expect(screen.getByText('Oct 28, 13 days old')).toBeInTheDocument();
    expect(screen.getByText('1 sent, 2 slots left')).toBeInTheDocument();
    expect(screen.getByText('Sends after 11:05 ET')).toBeInTheDocument();
  });
});

describe('skip', () => {
  it('s skips with Undo and commits once after 8 seconds', async () => {
    const { client } = await open(T.omar, 'Omar Haddad', { shell: true });
    const skip = vi.spyOn(client, 'skip');
    startClock();
    press('s');
    await settle();
    expect(within(row('Omar Haddad')).getByText('Skipped')).toBeInTheDocument();
    expect(sidebarOutbox()).toHaveAccessibleName('Outbox, 1 reply to handle, 6 drafts to review');
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();
    await settle(8000);
    expect(skip).toHaveBeenCalledTimes(1);
    expect(skip.mock.calls[0]?.slice(0, 2)).toEqual([messageOf(T.omar), 1]);
  });
});

describe('reply', () => {
  it('Priya: the hold alert names who is held and links to Gmail', async () => {
    await open(T.priya, 'Priya Natarajan');
    const alert = screen.getByText('Browserbase is on hold').closest<HTMLElement>('[role="alert"]') as HTMLElement;
    expect(alert).toHaveTextContent('Lena Szabo (approved)');
    expect(alert).toHaveTextContent('Tom Becker (follow-up 1, due today)');
    expect(alert).toHaveTextContent('This app never sends a reply.');
    const link = within(alert).getByRole('link', { name: /Open in Gmail/ });
    expect(link).toHaveAttribute('href', 'https://mail.google.com/mail/u/0/#inbox/thread-priya-natarajan');
    expect(within(screen.getByRole('article')).getByText(/Does Thursday at 2 PT work for 20 minutes\?/)).toBeInTheDocument();
    expect(within(screen.getByRole('group', { name: 'Reply kind' })).getByRole('button', { name: 'Interested' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
  });

  it('a closed company has no Close footer on its reply', async () => {
    const db = buildFixture();
    const priya = db.details[T.priya];
    if (!priya) throw new Error('No Priya in the fixture');
    priya.item.company.status = 'closed';
    priya.item.company.heldByTouchId = null;
    const client = new FixtureClient(db);
    renderWithApp(<OutboxPage />, { route: `/outbox/${T.priya}`, client });
    await screen.findByRole('heading', { level: 2, name: 'Priya Natarajan' });
    expect(screen.queryByRole('button', { name: /^Close Browserbase/ })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Resume Browserbase' })).toBeNull();
  });

  it('Not now is a pending write with Undo', async () => {
    const { client } = await open(T.priya, 'Priya Natarajan');
    const setReplyKind = vi.spyOn(client, 'setReplyKind');
    startClock();
    fireEvent.click(screen.getByRole('button', { name: 'Not now' }));
    await settle();
    expect(screen.getByRole('button', { name: 'Not now' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();
    expect(setReplyKind).not.toHaveBeenCalled();
    await settle(8000);
    expect(setReplyKind).toHaveBeenCalledTimes(1);
    expect(setReplyKind.mock.calls[0]?.slice(0, 3)).toEqual([T.priya, 'not_now', 1]);
  });

  it('a second choice inside the window replaces the first: Not now, then Said no commits only Said no, at once', async () => {
    const { client } = await open(T.priya, 'Priya Natarajan');
    const setReplyKind = vi.spyOn(client, 'setReplyKind');
    startClock();
    fireEvent.click(screen.getByRole('button', { name: 'Not now' }));
    await settle(1000);
    expect(setReplyKind).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Said no' }));
    await settle();
    expect(setReplyKind).toHaveBeenCalledTimes(1);
    expect(setReplyKind.mock.calls[0]?.slice(0, 3)).toEqual([T.priya, 'said_no', 1]);
    await settle(8000);
    expect(setReplyKind).toHaveBeenCalledTimes(1);
  });

  it('Not now, then Interested inside the window: the last choice wins and commits once', async () => {
    const { client } = await open(T.priya, 'Priya Natarajan');
    const setReplyKind = vi.spyOn(client, 'setReplyKind');
    startClock();
    fireEvent.click(screen.getByRole('button', { name: 'Not now' }));
    await settle(1000);
    fireEvent.click(screen.getByRole('button', { name: 'Interested' }));
    await settle();
    expect(screen.getByRole('button', { name: 'Interested' })).toHaveAttribute('aria-pressed', 'true');
    await settle(8000);
    expect(setReplyKind).toHaveBeenCalledTimes(1);
    expect(setReplyKind.mock.calls[0]?.slice(0, 3)).toEqual([T.priya, 'interested', 1]);
  });

  it('Asked to stop commits at once, with no Undo', async () => {
    const { client } = await open(T.priya, 'Priya Natarajan');
    const setReplyKind = vi.spyOn(client, 'setReplyKind');
    fireEvent.click(screen.getByRole('button', { name: 'Asked to stop' }));
    await waitFor(() => expect(setReplyKind).toHaveBeenCalledTimes(1));
    expect(setReplyKind.mock.calls[0]?.slice(0, 3)).toEqual([T.priya, 'asked_to_stop', 1]);
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Asked to stop' })).toHaveAttribute('aria-pressed', 'true'));
  });

  it('Close with a reason opens the dialog, and closing is an undoable write', async () => {
    const { client } = await open(T.priya, 'Priya Natarajan');
    const close = vi.spyOn(client, 'closeCompany');
    fireEvent.click(screen.getByRole('button', { name: /^Close Browserbase with a reason/ }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Close Browserbase')).toBeInTheDocument();
    startClock();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close company' }));
    await settle();
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();
    expect(close).not.toHaveBeenCalled();
    await settle(8000);
    expect(close).toHaveBeenCalledTimes(1);
    expect(close.mock.calls[0]?.slice(0, 3)).toEqual([companyId('Browserbase'), 'interview', '']);
  });

  it('Resume lists what will send again, then resumes at once', async () => {
    const { client } = await open(T.priya, 'Priya Natarajan');
    const resume = vi.spyOn(client, 'resumeCompany');
    fireEvent.click(screen.getByRole('button', { name: 'Resume Browserbase' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(dialog).toHaveTextContent('Lena Szabo (approved)');
    expect(dialog).toHaveTextContent('Tom Becker (follow-up 1, due today)');
    expect(resume).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Resume Browserbase' }));
    await waitFor(() => expect(resume).toHaveBeenCalledTimes(1));
    expect(resume.mock.calls[0]?.[0]).toBe(companyId('Browserbase'));
    await waitFor(() => expect(screen.queryByText('Browserbase is on hold')).toBeNull());
  });
});

describe('company actions', () => {
  it('p pauses the company at once and o opens its page', async () => {
    const { client } = await open(T.kenji, 'Kenji Watanabe');
    const pause = vi.spyOn(client, 'pauseCompany');
    expect(screen.getByRole('button', { name: 'Pause Trigger.dev' })).toBeInTheDocument();
    press('p');
    await waitFor(() => expect(pause).toHaveBeenCalledTimes(1));
    expect(pause.mock.calls[0]?.[0]).toBe(companyId('Trigger.dev'));
    expect(await screen.findByRole('button', { name: 'Resume Trigger.dev' })).toBeInTheDocument();
  });
});

describe('the company filter and the open email', () => {
  it('an email from another company is not shown while ?company= filters the list', async () => {
    renderWithApp(<OutboxPage />, { route: `/outbox/${T.kenji}?company=${companyId('Mintlify')}`, client: new FixtureClient(buildFixture()) });
    await screen.findByText('Showing only Mintlify');
    expect(screen.queryByRole('heading', { level: 2, name: 'Kenji Watanabe' })).toBeNull();
    expect(screen.getByText('No email selected')).toBeInTheDocument();
  });
});

describe('read-only emails', () => {
  it.each([
    [T.chloe, 'Chloe Martin'],
    [T.ben, 'Ben Ito'],
  ])('%s: Sending and Checking items show no action buttons or keys', async (touchId, name) => {
    const { client } = await open(touchId, name);
    const skip = vi.spyOn(client, 'skip');
    const unapprove = vi.spyOn(client, 'unapprove');
    const pause = vi.spyOn(client, 'pauseCompany');
    const detail = screen.getByRole('article');
    expect(within(detail).queryAllByRole('button')).toHaveLength(0);
    expect(within(detail).queryByRole('textbox')).toBeNull();
    for (const key of ['a', 's', 'u', 'p', 'c']) press(key);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(skip).not.toHaveBeenCalled();
    expect(unapprove).not.toHaveBeenCalled();
    expect(pause).not.toHaveBeenCalled();
  });

  it('a sent email shows its thread and the follow-up track', async () => {
    await open(T.theo, 'Theo Brandt');
    expect(screen.getByRole('group', { name: 'Follow-up schedule' })).toBeInTheDocument();
    expect(screen.getByText(/Your postgres branching post is the kind of work/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
  });
});

describe('leaving the editor', () => {
  it('Escape leaves the editor and returns focus to the list row', async () => {
    await open(T.omar, 'Omar Haddad');
    act(() => bodyBox().focus());
    expect(bodyBox()).toHaveFocus();
    press('Escape', {}, bodyBox());
    expect(bodyBox()).not.toHaveFocus();
    expect(row('Omar Haddad')).toHaveFocus();
  });

  it('e moves focus into the body', async () => {
    await open(T.omar, 'Omar Haddad');
    press('e');
    expect(bodyBox()).toHaveFocus();
  });
});
