import { act, fireEvent, screen } from '@testing-library/react';
import { ApiError, type OutboxItem } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture } from '@/api/fixture/data';
import { AppShell } from '@/shell/app-shell';
import { renderWithApp } from '@/test/render';
import { cleanDrafts, refusedSummary } from './approve-batch';
import OutboxPage from './index';
import { useApproveBatch } from './use-approve-batch';

function Probe({ drafts }: { drafts: OutboxItem[] }) {
  const approve = useApproveBatch();
  return (
    <button type="button" onClick={() => approve(drafts)}>
      Go
    </button>
  );
}

/** The Outbox with the sidebar (it owns the Z key) and a button that queues the clean drafts as they are now. */
async function setup() {
  const db = buildFixture();
  const client = new FixtureClient(db);
  const drafts = structuredClone(cleanDrafts(db.outbox.items, () => false));
  expect(drafts.length).toBeGreaterThan(2);
  renderWithApp(
    <>
      <AppShell />
      <OutboxPage />
      <Probe drafts={drafts} />
    </>,
    { route: '/outbox', client },
  );
  await screen.findByRole('group', { name: 'Sender' });
  // Only the undo window and Query's notifications are timed; the page loaded on the real clock.
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'requestAnimationFrame', 'cancelAnimationFrame'] });
  return { client, db, drafts };
}

const findRow = (name: string) => screen.queryByText(name, { selector: 'b' })?.closest<HTMLElement>('[role="option"]') ?? null;
const groupOf = (name: string) => findRow(name)?.closest('[data-group]')?.getAttribute('data-group');
const liveUndos = () => screen.queryAllByRole('button', { name: 'Undo' }).filter((b) => b.closest('[data-sonner-toast]')?.getAttribute('data-removed') !== 'true');
const toastText = () => [...document.querySelectorAll('[data-sonner-toast]')].map((t) => t.textContent ?? '').join('|');
const settle = (ms = 0) => act(() => vi.advanceTimersByTimeAsync(ms));
const go = () => fireEvent.click(screen.getByRole('button', { name: 'Go' }));

afterEach(async () => {
  if (vi.isFakeTimers()) {
    await act(() => vi.runOnlyPendingTimersAsync());
    vi.useRealTimers();
  }
  vi.restoreAllMocks();
});

describe('useApproveBatch', () => {
  it('queues one write: rows move to Approved at once, one Undo toast, no approve call yet', async () => {
    const { client, drafts } = await setup();
    const approve = vi.spyOn(client, 'approve');
    for (const d of drafts) expect(groupOf(d.person.name)).toBe('review');
    go();
    await settle();
    for (const d of drafts) expect(groupOf(d.person.name)).toBe('approved');
    expect(liveUndos()).toHaveLength(1);
    expect(toastText()).toContain(`Approved ${drafts.length} drafts.`);
    expect(approve).not.toHaveBeenCalled();
  });

  it('after 8 seconds approves every draft once, at the revision it showed', async () => {
    const { client, drafts } = await setup();
    const approve = vi.spyOn(client, 'approve');
    go();
    await settle(8000);
    expect(approve).toHaveBeenCalledTimes(drafts.length);
    for (const d of drafts) expect(approve).toHaveBeenCalledWith(d.messageId, d.revision, expect.objectContaining({ requestId: expect.any(String) }));
    const ids = approve.mock.calls.map((c) => (c[2] as { requestId: string }).requestId);
    expect(new Set(ids).size).toBe(drafts.length);
  });

  it('Undo drops the whole batch: nothing is approved and the rows come back', async () => {
    const { client, drafts } = await setup();
    const approve = vi.spyOn(client, 'approve');
    go();
    await settle();
    fireEvent.click(liveUndos()[0]!);
    await settle(8000);
    expect(approve).not.toHaveBeenCalled();
    for (const d of drafts) expect(groupOf(d.person.name)).toBe('review');
  });

  it('Z undoes the batch', async () => {
    const { client, drafts } = await setup();
    const approve = vi.spyOn(client, 'approve');
    go();
    await settle();
    fireEvent.keyDown(document.body, { key: 'z' });
    await settle(8000);
    expect(approve).not.toHaveBeenCalled();
    for (const d of drafts) expect(groupOf(d.person.name)).toBe('review');
  });

  it('a refused draft does not stop the rest, and the toast names it', async () => {
    const { client, drafts } = await setup();
    const real = client.approve.bind(client);
    const refused = drafts[1]!;
    const approve = vi.spyOn(client, 'approve').mockImplementation((id, rev, o) =>
      id === refused.messageId ? Promise.reject(new ApiError('conflict', 'The draft changed since you loaded it. Nothing was saved.')) : real(id, rev, o),
    );
    go();
    await settle(8000);
    await settle(1000); // the error toast renders after the commit settles
    expect(approve).toHaveBeenCalledTimes(drafts.length);
    const message = refusedSummary(drafts.length - 1, drafts.length, [{ name: refused.person.name, message: 'The draft changed since you loaded it.' }]);
    expect(toastText()).toContain(message);
    expect(groupOf(refused.person.name)).toBe('review');
    expect(groupOf(drafts[0]!.person.name)).toBe('approved');
    expect(groupOf(drafts[2]!.person.name)).toBe('approved');
  });

  it('a newer saved version is refused and named', async () => {
    const { db, drafts } = await setup();
    const newer = drafts[0]!;
    go();
    await settle();
    const live = db.outbox.items.find((i) => i.messageId === newer.messageId)!;
    live.revision += 1; // Claude saved a newer version while the window ran
    await settle(8000);
    await settle(1000);
    expect(toastText()).toContain(`${newer.person.name} was refused`);
    expect(groupOf(newer.person.name)).toBe('review');
  });

  it('a page that is closing starts every approve without waiting for the one before', async () => {
    const { client, drafts } = await setup();
    const approve = vi.spyOn(client, 'approve').mockReturnValue(new Promise(() => {}));
    go();
    await settle();
    await act(async () => {
      window.dispatchEvent(new Event('pagehide'));
    });
    expect(approve).toHaveBeenCalledTimes(drafts.length);
    for (const call of approve.mock.calls) expect(call[2]).toMatchObject({ keepalive: true });
  });
});
