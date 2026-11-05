import { act, fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { OutboxItem } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture, type FixtureDb } from '@/api/fixture/data';
import { AppShell } from '@/shell/app-shell';
import { renderWithApp } from '@/test/render';
import { approvableDrafts, batchSummary, cleanDrafts, heldBack, heldBackText, splitText } from './approve-batch';
import OutboxPage from './index';

const none = () => false;

/** The Outbox and the sidebar (it owns the Z key). `edit` changes the fixture before it renders. */
async function outbox(opts: { route?: string; edit?: (db: FixtureDb) => void } = {}) {
  const db = buildFixture();
  opts.edit?.(db);
  const client = new FixtureClient(db);
  renderWithApp(
    <>
      <AppShell />
      <OutboxPage />
    </>,
    { route: opts.route ?? '/outbox', client },
  );
  await screen.findByRole('group', { name: 'Sender' });
  const clean = structuredClone(cleanDrafts(db.outbox.items, none));
  const approvable = structuredClone(approvableDrafts(db.outbox.items, none));
  return { db, client, clean, approvable };
}

const press = (key: string, init: KeyboardEventInit = {}) => fireEvent.keyDown(document.body, { key, ...init });
const findRow = (name: string) => screen.queryByText(name, { selector: 'b' })?.closest<HTMLElement>('[role="option"]') ?? null;
const groupOf = (name: string) => findRow(name)?.closest('[data-group]')?.getAttribute('data-group');
const bar = () => screen.queryByRole('group', { name: 'Approve clean drafts' });
const dialog = () => screen.getByRole('dialog');
const liveUndos = () => screen.queryAllByRole('button', { name: 'Undo' }).filter((b) => b.closest('[data-sonner-toast]')?.getAttribute('data-removed') !== 'true');
const settle = (ms = 0) => act(() => vi.advanceTimersByTimeAsync(ms));
const startClock = () => vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'requestAnimationFrame', 'cancelAnimationFrame'] });
/** Clicks the bar's Review button (it counts every draft he may tick) and waits for the dialog. */
const openDialog = (reviewing: number) => {
  fireEvent.click(within(bar()!).getByRole('button', { name: `Review ${reviewing}` }));
  return screen.findByRole('dialog');
};
const title = (n: number) => (n === 0 ? 'Approve drafts' : `Approve ${n} ${n === 1 ? 'draft' : 'drafts'}`);
/** Every draft is warned: the live case that made the first version look broken. */
const allWarned = (db: FixtureDb) => {
  for (const i of cleanDrafts(db.outbox.items, none)) i.warnCount = 1;
};
const rowBoxes = () => within(dialog()).getAllByRole('checkbox').slice(1);

afterEach(async () => {
  if (vi.isFakeTimers()) {
    await act(() => vi.runOnlyPendingTimersAsync());
    vi.useRealTimers();
  }
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe('the Approve bar', () => {
  it('counts every draft he may tick, says how they split, and what stays for him', async () => {
    const { db, clean, approvable } = await outbox();
    const warned = approvable.length - clean.length;
    expect(warned).toBeGreaterThan(0);
    expect(within(bar()!).getByText(`${approvable.length} ready to approve`)).toBeInTheDocument();
    expect(within(bar()!).getByText(splitText(clean.length, warned)!)).toBeInTheDocument();
    const held = heldBackText(heldBack(db.outbox.items, none));
    expect(held).not.toBeNull();
    expect(within(bar()!).getByText(held!)).toBeInTheDocument();
  });

  it('shows when every draft has a warning, and says so', async () => {
    const { approvable } = await outbox({ edit: allWarned });
    expect(approvable.length).toBeGreaterThan(1);
    expect(within(bar()!).getByText(`${approvable.length} ready to approve`)).toBeInTheDocument();
    expect(within(bar()!).getByText(`All ${approvable.length} have warnings.`)).toBeInTheDocument();
  });

  it('opens the dialog and approves nothing', async () => {
    const { client, approvable } = await outbox();
    const approve = vi.spyOn(client, 'approve');
    await openDialog(approvable.length);
    expect(approve).not.toHaveBeenCalled();
    expect(liveUndos()).toHaveLength(0);
    for (const d of approvable) expect(groupOf(d.person.name)).toBe('review');
  });

  it('is gone when every draft is blocked, and Shift+A does nothing', async () => {
    await outbox({
      edit: (db) => {
        for (const i of approvableDrafts(db.outbox.items, none)) i.blockCount = 1;
      },
    });
    expect(bar()).toBeNull();
    press('A', { shiftKey: true });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('Shift+A opens the dialog', async () => {
    const { clean } = await outbox();
    press('A', { shiftKey: true });
    expect(await screen.findByRole('dialog', { name: title(clean.length) })).toBeInTheDocument();
  });
});

describe('the dialog', () => {
  it('lists clean drafts first and ticked, then warned drafts unticked with their warning', async () => {
    const { clean, approvable } = await outbox();
    const d = await openDialog(approvable.length);
    expect(within(d).getByRole('heading', { name: title(clean.length) })).toBeInTheDocument();
    expect(within(d).getByText(batchSummary(clean))).toBeInTheDocument();
    const boxes = rowBoxes();
    expect(boxes).toHaveLength(approvable.length);
    boxes.forEach((box, n) => (n < clean.length ? expect(box).toBeChecked() : expect(box).not.toBeChecked()));
    for (const c of approvable) {
      expect(within(d).getByText(c.person.name)).toBeInTheDocument();
      expect(within(d).getByText(c.subject)).toBeInTheDocument();
    }
    for (const w of approvable.slice(clean.length)) expect(within(d).getAllByText(w.summary).length).toBeGreaterThan(0);
    expect(within(d).getByRole('button', { name: `Approve ${clean.length}` })).not.toHaveFocus();
  });

  it('every draft warned: nothing is ticked, Select all ticks them, and confirming approves them all', async () => {
    const { client, approvable } = await outbox({ edit: allWarned });
    const approve = vi.spyOn(client, 'approve');
    const d = await openDialog(approvable.length);
    for (const box of rowBoxes()) expect(box).not.toBeChecked();
    expect(within(d).getByRole('button', { name: 'Approve' })).toBeDisabled();
    fireEvent.click(within(d).getByRole('checkbox', { name: 'Select all' }));
    expect(within(d).getByText(new RegExp(`${approvable.length} have warnings`))).toBeInTheDocument();
    startClock();
    fireEvent.click(within(d).getByRole('button', { name: `Approve ${approvable.length}` }));
    await settle(8000);
    expect(approve).toHaveBeenCalledTimes(approvable.length);
    for (const a of approvable) expect(approve.mock.calls.map((c) => c[0])).toContain(a.messageId);
  });

  it('unticking one drops it: the title, the button and the batch follow', async () => {
    const { client, clean, approvable } = await outbox();
    const approve = vi.spyOn(client, 'approve');
    const d = await openDialog(approvable.length);
    fireEvent.click(rowBoxes()[0]!);
    const n = clean.length - 1;
    expect(within(d).getByRole('heading', { name: title(n) })).toBeInTheDocument();
    startClock();
    fireEvent.click(within(d).getByRole('button', { name: `Approve ${n}` }));
    await settle(8000);
    expect(approve).toHaveBeenCalledTimes(n);
    expect(approve.mock.calls.map((c) => c[0])).not.toContain(clean[0]!.messageId);
    expect(groupOf(clean[0]!.person.name)).toBe('review');
  });

  it('Select all ticks and unticks every row, and at zero the primary is disabled', async () => {
    const { approvable } = await outbox();
    const d = await openDialog(approvable.length);
    const all = within(d).getByRole('checkbox', { name: 'Select all' });
    fireEvent.click(all); // some were ticked, so this ticks the rest
    for (const box of rowBoxes()) expect(box).toBeChecked();
    expect(within(d).getByRole('button', { name: `Approve ${approvable.length}` })).toBeEnabled();
    fireEvent.click(all);
    for (const box of rowBoxes()) expect(box).not.toBeChecked();
    expect(within(d).getByRole('button', { name: 'Approve' })).toBeDisabled();
  });

  it('Cancel and Esc close it and change nothing', async () => {
    const { client, approvable } = await outbox();
    const approve = vi.spyOn(client, 'approve');
    const d = await openDialog(approvable.length);
    fireEvent.click(within(d).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    await openDialog(approvable.length);
    press('Escape');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(approve).not.toHaveBeenCalled();
    for (const c of approvable) expect(groupOf(c.person.name)).toBe('review');
  });

  it('confirming moves the ticked rows to Approved at once with one Undo, and Z brings them back', async () => {
    const { client, clean, approvable } = await outbox();
    const approve = vi.spyOn(client, 'approve');
    const d = await openDialog(approvable.length);
    startClock();
    fireEvent.click(within(d).getByRole('button', { name: `Approve ${clean.length}` }));
    await settle();
    expect(screen.queryByRole('dialog')).toBeNull();
    for (const c of clean) expect(groupOf(c.person.name)).toBe('approved');
    for (const w of approvable.slice(clean.length)) expect(groupOf(w.person.name)).toBe('review');
    expect(liveUndos()).toHaveLength(1);
    expect(approve).not.toHaveBeenCalled();
    press('z');
    await settle(8000);
    expect(approve).not.toHaveBeenCalled();
    for (const c of clean) expect(groupOf(c.person.name)).toBe('review');
  });
});

describe('what the dialog offers', () => {
  it('a draft with unsaved text is not counted or listed', async () => {
    const { clean, approvable } = await outbox();
    const typed: OutboxItem = clean[0]!;
    fireEvent.click(findRow(typed.person.name)!);
    const box = (await screen.findByRole('textbox', { name: 'Email body' })) as HTMLTextAreaElement;
    act(() => {
      box.focus();
      box.setSelectionRange(box.value.length, box.value.length);
    });
    await userEvent.setup().keyboard(' more');
    expect(await within(bar()!).findByText(`${approvable.length - 1} ready to approve`)).toBeInTheDocument();
    expect(within(bar()!).getByText(/with unsaved edits/)).toBeInTheDocument();
    const d = await openDialog(approvable.length - 1);
    expect(within(d).queryByText(typed.person.name)).toBeNull();
  });

  it('the company filter limits the dialog to that company', async () => {
    const all = approvableDrafts(buildFixture().outbox.items, none);
    const company = all[0]!.company.id;
    const mine = all.filter((c) => c.company.id === company);
    expect(mine.length).toBeLessThan(all.length);
    await outbox({ route: `/outbox?company=${company}` });
    expect(within(bar()!).getByText(`${mine.length} ready to approve`)).toBeInTheDocument();
    const d = await openDialog(mine.length);
    expect(rowBoxes()).toHaveLength(mine.length);
    for (const c of all.filter((x) => x.company.id !== company)) expect(within(d).queryByText(c.person.name)).toBeNull();
  });
});
