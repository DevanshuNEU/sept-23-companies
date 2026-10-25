import { act, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useEffect } from 'react';
import { Toaster } from '@/components/ui/sonner';
import { ApiError, type WriteOptions } from '@/api';
import {
  PendingWritesProvider,
  usePendingControls,
  usePendingWrite,
  useViewQuery,
  type Patch,
  type PendingWrite,
} from '@/pending/pending';
import { withRevision } from '@/pending/revisions';

interface Item {
  status: 'drafted' | 'approved';
  starred: boolean;
}
interface Shell {
  drafts: number;
}

const ITEM = ['item'] as const;
const SHELL = ['shell'] as const;

let server: { item: Item; shell: Shell };
let fetchItem: ReturnType<typeof vi.fn<() => Promise<Item>>>;
let fetchShell: ReturnType<typeof vi.fn<() => Promise<Shell>>>;

// The hooks under test, captured from inside the provider once it has rendered.
const api: {
  write?: (w: PendingWrite) => void;
  undoLast?: () => boolean;
  flushPending?: () => Promise<void>;
  cancel?: (id: string) => boolean;
  isPending?: (id: string) => boolean;
} = {};

function View({ pollMs }: { pollMs?: number }) {
  const item = useViewQuery(ITEM, fetchItem, { pollMs });
  const shell = useViewQuery(SHELL, fetchShell);
  const write = usePendingWrite();
  const { undoLast, flushPending, cancel, isPending } = usePendingControls();
  useEffect(() => {
    api.write = write;
    api.undoLast = undoLast;
    api.flushPending = flushPending;
    api.cancel = cancel;
    api.isPending = isPending;
  });
  return (
    <>
      <p data-testid="status">{item.data?.status ?? 'loading'}</p>
      <p data-testid="starred">{item.data ? String(item.data.starred) : 'loading'}</p>
      <p data-testid="drafts">{shell.data ? String(shell.data.drafts) : 'loading'}</p>
    </>
  );
}

// Fake timers turn a 0 ms timeout set during a tick into 1 ms, so work queued by a fetch or a
// rejected commit (Query's notify, Sonner's toast) needs a little time, not just a tick.
async function settle(ms = 0) {
  await act(() => vi.advanceTimersByTimeAsync(ms));
}

async function setup(opts: { pollMs?: number } = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <PendingWritesProvider>
        <View pollMs={opts.pollMs} />
        <Toaster />
      </PendingWritesProvider>
    </QueryClientProvider>,
  );
  await settle();
}

const approvePatch: Patch<Item> = { key: ITEM, apply: (d) => ({ ...d, status: 'approved' }) };
const starPatch: Patch<Item> = { key: ITEM, apply: (d) => ({ ...d, starred: true }) };
const shellPatch: Patch<Shell> = { key: SHELL, apply: (d) => ({ drafts: d.drafts - 1 }) };

function approveCommit() {
  return vi.fn<(o: WriteOptions) => Promise<unknown>>(async () => {
    server.item = { ...server.item, status: 'approved' };
    server.shell = { drafts: server.shell.drafts - 1 };
    return undefined;
  });
}

function approve(commit: (o: WriteOptions) => Promise<unknown>, extra: Partial<PendingWrite> = {}) {
  act(() =>
    api.write!({
      id: 'approve:m12',
      label: 'Approved Omar Haddad.',
      afterCommit: 'Approved. Press U to withdraw.',
      patches: [approvePatch, shellPatch],
      commit,
      ...extra,
    }),
  );
}

const text = (id: string) => screen.getByTestId(id).textContent;

beforeEach(() => {
  vi.useFakeTimers();
  server = { item: { status: 'drafted', starred: false }, shell: { drafts: 7 } };
  fetchItem = vi.fn(async () => ({ ...server.item }));
  fetchShell = vi.fn(async () => ({ ...server.shell }));
});

afterEach(async () => {
  await act(async () => {
    await vi.runOnlyPendingTimersAsync();
  });
  vi.useRealTimers();
  vi.restoreAllMocks();
  delete api.write;
  delete api.undoLast;
  delete api.flushPending;
  delete api.cancel;
  delete api.isPending;
});

describe('pending writes', () => {
  it('commits after 8 seconds', async () => {
    await setup();
    const commit = approveCommit();
    approve(commit);
    await settle();
    expect(text('status')).toBe('approved');
    expect(text('drafts')).toBe('6');
    expect(screen.getByText('Approved Omar Haddad.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();

    await settle(7999);
    expect(commit).not.toHaveBeenCalled();
    await settle(1);
    expect(commit).toHaveBeenCalledTimes(1);
    expect(commit).toHaveBeenCalledWith({ requestId: expect.any(String) });
    await settle();
    expect(text('status')).toBe('approved');
    expect(text('drafts')).toBe('6');
  });

  it('patches every key it names at once, the shell counts included', async () => {
    await setup();
    expect(text('drafts')).toBe('7');
    approve(approveCommit());
    await settle();
    expect(text('drafts')).toBe('6');
    expect(fetchShell).toHaveBeenCalledTimes(1);
  });

  it('undo inside the window drops the write', async () => {
    await setup();
    const commit = approveCommit();
    approve(commit);
    await settle(3000);
    expect(text('status')).toBe('approved');

    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    await settle();
    expect(text('status')).toBe('drafted');
    expect(text('drafts')).toBe('7');

    await settle(10000);
    expect(commit).not.toHaveBeenCalled();
    expect(api.undoLast!()).toBe(false);
  });

  it('undoLast takes back the most recent of two', async () => {
    await setup();
    const approveIt = approveCommit();
    const starIt = vi.fn(async () => undefined);
    approve(approveIt);
    act(() =>
      api.write!({ id: 'star:m12', label: 'Starred.', afterCommit: 'Starred.', patches: [starPatch], commit: starIt }),
    );
    await settle();
    expect(text('status')).toBe('approved');
    expect(text('starred')).toBe('true');

    let undone = false;
    act(() => {
      undone = api.undoLast!();
    });
    await settle();
    expect(undone).toBe(true);
    expect(text('starred')).toBe('false');
    expect(text('status')).toBe('approved');

    await settle(8000);
    expect(approveIt).toHaveBeenCalledTimes(1);
    expect(starIt).not.toHaveBeenCalled();
  });

  it('a poll inside the window keeps the overlay', async () => {
    await setup({ pollMs: 1000 });
    const commit = approveCommit();
    approve(commit);
    await settle(3000);
    expect(fetchItem.mock.calls.length).toBeGreaterThanOrEqual(3);
    await expect(fetchItem.mock.results.at(-1)!.value).resolves.toEqual({ status: 'drafted', starred: false });
    expect(text('status')).toBe('approved');

    await settle(5000);
    expect(commit).toHaveBeenCalledTimes(1);
    await settle();
    expect(text('status')).toBe('approved');

    // The refetch after the commit has landed, so the overlay is gone: the server's word shows again.
    server.item = { ...server.item, status: 'drafted' };
    await settle(1000);
    await settle(50);
    expect(text('status')).toBe('drafted');
  });

  it('a second action on a pending item queues nothing', async () => {
    await setup();
    const first = approveCommit();
    const second = approveCommit();
    approve(first);
    approve(second);
    await settle();
    expect(screen.getAllByRole('button', { name: 'Undo' })).toHaveLength(1);

    await settle(8000);
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).not.toHaveBeenCalled();
  });

  it('a silent immediate write commits at once with its patches and no toast', async () => {
    await setup();
    const commit = approveCommit();
    approve(commit, { immediate: true, silent: true });
    await settle();
    expect(text('status')).toBe('approved');
    expect(commit).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('Approved Omar Haddad.')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Undo' })).not.toBeInTheDocument();
  });

  it('isPending is true from the queue until the commit has settled, and false after an undo', async () => {
    await setup();
    expect(api.isPending!('approve:m12')).toBe(false);
    approve(approveCommit());
    await settle();
    expect(api.isPending!('approve:m12')).toBe(true);
    expect(api.isPending!('approve:other')).toBe(false);
    await settle(8000);
    await settle();
    expect(api.isPending!('approve:m12')).toBe(false);

    approve(approveCommit(), { id: 'approve:m13' });
    await settle();
    expect(api.isPending!('approve:m13')).toBe(true);
    act(() => {
      api.undoLast!();
    });
    expect(api.isPending!('approve:m13')).toBe(false);
  });

  it('cancel drops a pending write by id: no commit, overlay and toast gone', async () => {
    await setup();
    const commit = approveCommit();
    approve(commit);
    await settle();
    expect(text('status')).toBe('approved');
    expect(text('drafts')).toBe('6');

    let cancelled = false;
    act(() => {
      cancelled = api.cancel!('approve:m12');
    });
    await settle();
    expect(cancelled).toBe(true);
    expect(text('status')).toBe('drafted');
    expect(text('drafts')).toBe('7');
    await settle(1000);
    // Sonner marks a dismissed toast removed and unmounts it when its exit transition ends (jsdom has none).
    expect(screen.queryByText('Approved Omar Haddad.')?.closest('[data-sonner-toast]')).toHaveAttribute('data-removed', 'true');

    await settle(10000);
    await act(() => api.flushPending!());
    expect(commit).not.toHaveBeenCalled();
  });

  it('cancel leaves other writes alone, and the id can be queued again', async () => {
    await setup();
    const approveIt = approveCommit();
    const starIt = vi.fn(async () => undefined);
    approve(approveIt);
    act(() =>
      api.write!({ id: 'star:m12', label: 'Starred.', afterCommit: 'Starred.', patches: [starPatch], commit: starIt }),
    );
    await settle();

    act(() => {
      api.cancel!('approve:m12');
    });
    await settle();
    expect(text('status')).toBe('drafted');
    expect(text('starred')).toBe('true');

    const again = approveCommit();
    approve(again);
    await settle(8000);
    expect(starIt).toHaveBeenCalledTimes(1);
    expect(again).toHaveBeenCalledTimes(1);
    expect(approveIt).not.toHaveBeenCalled();
  });

  it('cancel does nothing for an unknown id or a write that already committed', async () => {
    await setup();
    const commit = approveCommit();
    approve(commit);
    await settle();
    expect(api.cancel!('approve:other')).toBe(false);

    await settle(8000);
    expect(commit).toHaveBeenCalledTimes(1);
    expect(api.cancel!('approve:m12')).toBe(false);
    await settle();
    expect(text('status')).toBe('approved');
  });

  it('flushes pending writes on pagehide', async () => {
    await setup();
    const commit = approveCommit();
    approve(commit);
    act(() => {
      window.dispatchEvent(new Event('pagehide'));
    });
    expect(commit).toHaveBeenCalledTimes(1);
    expect(commit).toHaveBeenCalledWith({ requestId: expect.any(String), keepalive: true });
  });

  it('pagehide then 8 s: commit once', async () => {
    await setup();
    const commit = approveCommit();
    approve(commit);
    act(() => {
      window.dispatchEvent(new Event('pagehide'));
    });
    await settle(10000);
    expect(commit).toHaveBeenCalledTimes(1);
  });

  it('flush twice: once', async () => {
    await setup();
    const commit = approveCommit();
    approve(commit);
    await act(async () => {
      await Promise.all([api.flushPending!(), api.flushPending!()]);
    });
    await act(() => api.flushPending!());
    await settle(10000);
    expect(commit).toHaveBeenCalledTimes(1);
    expect(commit).toHaveBeenCalledWith({ requestId: expect.any(String) });
  });

  it('after a flush the toast drops Undo', async () => {
    await setup();
    approve(approveCommit());
    await settle();
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();

    await act(() => api.flushPending!());
    await settle();
    expect(screen.getByText('Approved. Press U to withdraw.')).toBeInTheDocument();
    expect(screen.queryByText('Approved Omar Haddad.')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Undo' })).not.toBeInTheDocument();
    expect(api.undoLast!()).toBe(false);
  });

  it('requestId is fixed at action time', async () => {
    let n = 0;
    const uuid = vi.spyOn(crypto, 'randomUUID').mockImplementation(() => {
      n += 1;
      return `00000000-0000-4000-8000-00000000000${n}` as `${string}-${string}-${string}-${string}-${string}`;
    });
    await setup();
    const commit = approveCommit();
    const before = uuid.mock.calls.length;
    approve(commit);
    expect(uuid.mock.calls.length).toBe(before + 1);
    const created = uuid.mock.results.at(-1)!.value as string;

    await settle(8000);
    expect(commit).toHaveBeenCalledWith({ requestId: created });
  });

  it('immediate writes commit at once without Undo', async () => {
    await setup();
    const commit = approveCommit();
    approve(commit, { immediate: true, label: 'Marked as asked to stop.' });
    expect(commit).toHaveBeenCalledTimes(1);
    expect(commit).toHaveBeenCalledWith({ requestId: expect.any(String), keepalive: true });
    await settle();
    expect(screen.getByText('Marked as asked to stop.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Undo' })).not.toBeInTheDocument();
    expect(text('status')).toBe('approved');
    expect(api.undoLast!()).toBe(false);

    await settle(10000);
    expect(commit).toHaveBeenCalledTimes(1);
  });

  it('a failed commit reverts and shows the error', async () => {
    await setup();
    const commit = vi.fn(async () => {
      throw new ApiError('blocked', 'Blocked: the company is on the no-go list.');
    });
    approve(commit);
    await settle();
    expect(text('status')).toBe('approved');
    expect(text('drafts')).toBe('6');
    const itemFetches = fetchItem.mock.calls.length;

    await settle(8000);
    expect(commit).toHaveBeenCalledTimes(1);
    await settle(50);
    expect(text('status')).toBe('drafted');
    expect(text('drafts')).toBe('7');
    expect(screen.getByText('Blocked: the company is on the no-go list.')).toBeInTheDocument();
    expect(fetchItem.mock.calls.length).toBeGreaterThan(itemFetches);
  });
});

describe('immediate writes (N1)', () => {
  it('an immediate write with a known revision is sent with keepalive true and no read first', async () => {
    await setup();
    const read = vi.fn(() => new Promise<number>(() => {}));
    const send = vi.fn<(revision: number) => Promise<undefined>>(async () => undefined);
    approve((o) => withRevision(o, () => 7, read, send), { immediate: true });
    // The same task: nothing was awaited between the click and the send.
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith(7);
    expect(read).not.toHaveBeenCalled();
  });
});

describe('flush order (N3)', () => {
  /** A commit that records when it starts and ends, and ends only when `finish` is called. */
  function steps(log: string[], name: string) {
    let finish!: () => void;
    const done = new Promise<void>((resolve) => (finish = resolve));
    const commit = vi.fn<(o: WriteOptions) => Promise<void>>(async () => {
      log.push(`${name}:start`);
      await done;
      log.push(`${name}:end`);
    });
    return { commit, finish };
  }
  const queue = (name: string, commit: (o: WriteOptions) => Promise<unknown>) =>
    approve(commit, { id: `${name}:m12`, patches: [], label: `${name} queued.`, afterCommit: `${name} sent.` });

  it('a skip flushed while an approve is in flight waits for it and carries its revision', async () => {
    await setup();
    let revision = 1;
    let finishApprove!: () => void;
    const approveDone = new Promise<void>((resolve) => (finishApprove = resolve));
    const approveCommitFn = vi.fn<(o: WriteOptions) => Promise<void>>(async () => {
      await approveDone;
      revision = 2;
    });
    const skipSawRevision: number[] = [];
    const skip = vi.fn<(o: WriteOptions) => Promise<void>>(async () => void skipSawRevision.push(revision));

    queue('approve', approveCommitFn);
    await settle(8000);
    expect(approveCommitFn).toHaveBeenCalledTimes(1);
    queue('skip', skip);
    let flushed = false;
    act(() => {
      void api.flushPending!().then(() => (flushed = true));
    });
    await settle(50);
    expect(skip).not.toHaveBeenCalled();
    expect(flushed).toBe(false);

    finishApprove();
    await settle(50);
    expect(skip).toHaveBeenCalledTimes(1);
    expect(skipSawRevision).toEqual([2]);
    await settle(50);
    expect(flushed).toBe(true);
  });

  it('flushed commits run in queue order, one after another', async () => {
    await setup();
    const log: string[] = [];
    const a = steps(log, 'a');
    const b = steps(log, 'b');
    const c = steps(log, 'c');
    queue('a', a.commit);
    queue('b', b.commit);
    queue('c', c.commit);
    act(() => void api.flushPending!());
    await settle();
    expect(log).toEqual(['a:start']);
    a.finish();
    await settle();
    expect(log).toEqual(['a:start', 'a:end', 'b:start']);
    b.finish();
    await settle();
    c.finish();
    await settle();
    expect(log).toEqual(['a:start', 'a:end', 'b:start', 'b:end', 'c:start', 'c:end']);
  });

  /** An approve past its window and still waiting for the server. */
  async function inFlight(log: string[]) {
    const first = steps(log, 'first');
    queue('first', first.commit);
    await settle(8000);
    expect(log).toEqual(['first:start']);
    return first;
  }
  /** Undo buttons in toasts that are not on their way out (a dismissed toast lingers for its exit animation). */
  const liveUndos = () => screen.queryAllByRole('button', { name: 'Undo' }).filter((b) => b.closest('[data-sonner-toast]')?.getAttribute('data-removed') !== 'true');
  const startFlush = () => act(() => void api.flushPending!());

  it('a pagehide during a flush\'s wait still sends every held write, once each, in queue order', async () => {
    await setup();
    const log: string[] = [];
    const first = await inFlight(log);
    const b = steps(log, 'b');
    const c = steps(log, 'c');
    queue('b', b.commit);
    queue('c', c.commit);
    startFlush();
    await settle();
    expect(log).toEqual(['first:start']);

    act(() => {
      window.dispatchEvent(new Event('pagehide'));
    });
    expect(log).toEqual(['first:start', 'b:start', 'c:start']);
    expect(b.commit).toHaveBeenCalledWith({ requestId: expect.any(String), keepalive: true });
    expect(c.commit).toHaveBeenCalledWith({ requestId: expect.any(String), keepalive: true });

    first.finish();
    b.finish();
    c.finish();
    await settle(50);
    expect(b.commit).toHaveBeenCalledTimes(1);
    expect(c.commit).toHaveBeenCalledTimes(1);
  });

  it('two overlapping flushes keep queue order', async () => {
    await setup();
    const log: string[] = [];
    const first = await inFlight(log);
    const b = steps(log, 'b');
    const c = steps(log, 'c');
    queue('b', b.commit);
    startFlush();
    queue('c', c.commit);
    startFlush();
    await settle();
    expect(log).toEqual(['first:start']);

    first.finish();
    await settle();
    expect(log).toEqual(['first:start', 'first:end', 'b:start']);
    b.finish();
    await settle();
    expect(log).toEqual(['first:start', 'first:end', 'b:start', 'b:end', 'c:start']);
    c.finish();
    await settle();
    expect(b.commit).toHaveBeenCalledTimes(1);
    expect(c.commit).toHaveBeenCalledTimes(1);
  });

  it('Undo is not offered for a held entry during the wait, and the write is sent', async () => {
    await setup();
    const log: string[] = [];
    const first = await inFlight(log);
    const b = steps(log, 'b');
    queue('b', b.commit);
    await settle();
    expect(liveUndos()).toHaveLength(1);
    startFlush();
    await settle();
    expect(screen.getByText('b sent.')).toBeInTheDocument();
    expect(liveUndos()).toHaveLength(0);

    first.finish();
    await settle();
    expect(b.commit).toHaveBeenCalledTimes(1);
    b.finish();
    await settle();
  });

  it('a flush on pagehide still starts every commit in one pass, in queue order, and does not wait for one in flight', async () => {
    await setup();
    const log: string[] = [];
    const early = steps(log, 'early');
    queue('early', early.commit);
    await settle(8000);
    const a = steps(log, 'a');
    const b = steps(log, 'b');
    queue('a', a.commit);
    queue('b', b.commit);
    act(() => {
      window.dispatchEvent(new Event('pagehide'));
    });
    expect(log).toEqual(['early:start', 'a:start', 'b:start']);
    expect(a.commit).toHaveBeenCalledWith({ requestId: expect.any(String), keepalive: true });
    early.finish();
    a.finish();
    b.finish();
    await settle();
  });
});
