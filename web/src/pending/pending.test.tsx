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
} = {};

function View({ pollMs }: { pollMs?: number }) {
  const item = useViewQuery(ITEM, fetchItem, { pollMs });
  const shell = useViewQuery(SHELL, fetchShell);
  const write = usePendingWrite();
  const { undoLast, flushPending } = usePendingControls();
  useEffect(() => {
    api.write = write;
    api.undoLast = undoLast;
    api.flushPending = flushPending;
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
    expect(commit).toHaveBeenCalledWith({ requestId: expect.any(String) });
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
