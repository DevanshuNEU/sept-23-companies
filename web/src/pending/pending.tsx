import { createContext, useCallback, useContext, useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from 'react';
import { hashKey, useQuery, useQueryClient, type QueryClient, type UseQueryResult } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ApiError, newRequestId, type WriteOptions } from '@/api';

/**
 * An optimistic change to one query's data. `apply` is a method so a `Patch<OutboxView>` fits in a
 * `Patch[]` (method parameters are bivariant).
 */
export type Patch<T = unknown> = { key: readonly unknown[]; apply(data: T): T };

export interface PendingWrite {
  /** Names the thing acted on (`approve:m12`). A second write with an id that is still pending is ignored. */
  id: string;
  /** Toast text while the write waits in its window. */
  label: string;
  /** Toast text once the write is flushed early (view change or pagehide): it can no longer be undone here. */
  afterCommit: string;
  patches: Patch[];
  commit: (o: WriteOptions) => Promise<unknown>;
  /** Commit at once, with the patches and a toast but no Undo. */
  immediate?: boolean;
}

interface Overlay {
  owner: string;
  hash: string;
  apply: (data: unknown) => unknown;
}

interface Queued {
  write: PendingWrite;
  requestId: string;
  toastId: string;
  timer: ReturnType<typeof setTimeout>;
}

// The spec's rule for errors: what happened, what did not happen, what to do.
const FALLBACK_ERROR = 'The change did not go through. Nothing was saved. Try again.';

/**
 * Holds the writes inside their undo window and the overlay of patches. The overlay is not a cache
 * edit: the query cache keeps what the server said, and `useViewQuery` lays the patches over every
 * result (polls and refetch on focus included) until the refetch that starts after the commit lands.
 */
class PendingStore {
  private overlays: readonly Overlay[] = [];
  private readonly listeners = new Set<() => void>();
  /** Writes still inside their window, oldest first. */
  private queue: Queued[] = [];
  /** Ids from the moment a write is queued until its commit and refetch settle. */
  private readonly active = new Set<string>();

  constructor(private readonly queryClient: QueryClient) {}

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  snapshot = (): readonly Overlay[] => this.overlays;

  enqueue(write: PendingWrite, windowMs: number): void {
    if (this.active.has(write.id)) return;
    this.active.add(write.id);
    const requestId = newRequestId();
    const toastId = `pending-${requestId}`;
    this.setOverlays([
      ...this.overlays,
      ...write.patches.map((p) => ({ owner: requestId, hash: hashKey(p.key), apply: (d: unknown) => p.apply(d) })),
    ]);

    if (write.immediate) {
      toast(write.label, { id: toastId });
      void this.run(write, requestId, toastId, { requestId });
      return;
    }

    const entry: Queued = {
      write,
      requestId,
      toastId,
      timer: setTimeout(() => {
        if (!this.take(entry)) return;
        toast.dismiss(toastId);
        void this.run(write, requestId, toastId, { requestId });
      }, windowMs),
    };
    this.queue.push(entry);
    toast(write.label, {
      id: toastId,
      duration: windowMs,
      action: { label: 'Undo', onClick: () => this.undo(entry) },
    });
  }

  undoLast(): boolean {
    const last = this.queue.at(-1);
    return last ? this.undo(last) : false;
  }

  /** Drops the write with this id if it is still in its window: no commit, overlay and toast removed. False when there is none. */
  cancel(id: string): boolean {
    const entry = this.queue.find((e) => e.write.id === id);
    return entry ? this.undo(entry) : false;
  }

  /** True from the moment a write with this id is queued until its commit and refetch settle (an undo ends it too). */
  isPending(id: string): boolean {
    return this.active.has(id);
  }

  /** Commits every write still in its window at once. Each leaves the queue before its commit starts. */
  async flush(keepalive = false): Promise<void> {
    const entries = this.queue;
    this.queue = [];
    for (const e of entries) clearTimeout(e.timer);
    await Promise.all(
      entries.map((e) => {
        toast(e.write.afterCommit, { id: e.toastId, action: undefined });
        const o: WriteOptions = keepalive ? { requestId: e.requestId, keepalive: true } : { requestId: e.requestId };
        return this.run(e.write, e.requestId, e.toastId, o);
      }),
    );
  }

  /** Clears the timers when the provider goes away (tests, hot reload). A page close flushes on pagehide first. */
  dispose(): void {
    for (const e of this.queue) clearTimeout(e.timer);
    this.queue = [];
  }

  private undo(entry: Queued): boolean {
    if (!this.take(entry)) return false;
    this.dropOverlays(entry.requestId);
    this.active.delete(entry.write.id);
    toast.dismiss(entry.toastId);
    return true;
  }

  /** Removes a write from the queue and stops its timer. False when it already left (committed or undone). */
  private take(entry: Queued): boolean {
    const i = this.queue.indexOf(entry);
    if (i === -1) return false;
    this.queue.splice(i, 1);
    clearTimeout(entry.timer);
    return true;
  }

  private async run(write: PendingWrite, requestId: string, toastId: string, o: WriteOptions): Promise<void> {
    const keys = uniqueKeys(write.patches);
    try {
      await write.commit(o);
    } catch (err) {
      this.dropOverlays(requestId);
      this.active.delete(write.id);
      toast.dismiss(toastId);
      toast.error(err instanceof ApiError ? err.message : FALLBACK_ERROR);
      await this.refetch(keys);
      return;
    }
    await this.refetch(keys);
    this.dropOverlays(requestId);
    this.active.delete(write.id);
  }

  /** Resolves once active queries for these keys have refetched; a poll already in flight is cancelled and restarted. */
  private async refetch(keys: readonly (readonly unknown[])[]): Promise<void> {
    await Promise.all(keys.map((queryKey) => this.queryClient.invalidateQueries({ queryKey, exact: true })));
  }

  private dropOverlays(owner: string): void {
    this.setOverlays(this.overlays.filter((o) => o.owner !== owner));
  }

  private setOverlays(next: readonly Overlay[]): void {
    this.overlays = next;
    for (const l of this.listeners) l();
  }
}

function uniqueKeys(patches: readonly Patch[]): (readonly unknown[])[] {
  const byHash = new Map<string, readonly unknown[]>();
  for (const p of patches) byHash.set(hashKey(p.key), p.key);
  return [...byHash.values()];
}

interface PendingContextValue {
  store: PendingStore;
  windowMs: number;
}

const PendingContext = createContext<PendingContextValue | null>(null);

export function PendingWritesProvider({ children, windowMs = 8000 }: { children: ReactNode; windowMs?: number }) {
  const queryClient = useQueryClient();
  const [store] = useState(() => new PendingStore(queryClient));

  useEffect(() => {
    const onPageHide = () => void store.flush(true);
    window.addEventListener('pagehide', onPageHide);
    return () => {
      window.removeEventListener('pagehide', onPageHide);
      store.dispose();
    };
  }, [store]);

  const value = useMemo(() => ({ store, windowMs }), [store, windowMs]);
  return <PendingContext.Provider value={value}>{children}</PendingContext.Provider>;
}

function usePendingContext(): PendingContextValue {
  const value = useContext(PendingContext);
  if (!value) throw new Error('pending writes outside PendingWritesProvider');
  return value;
}

/** Queues an undoable write: patches apply at once, a toast offers Undo, the commit runs when the window ends. */
// eslint-disable-next-line react-refresh/only-export-components
export function usePendingWrite(): (w: PendingWrite) => void {
  const { store, windowMs } = usePendingContext();
  return useCallback((w: PendingWrite) => store.enqueue(w, windowMs), [store, windowMs]);
}

/**
 * `undoLast` (bound to `z`) drops the newest write still in its window; `flushPending` commits them all now;
 * `cancel(id)` drops the write with that id if it is still in its window (a view uses it when the next action
 * on a thing is the opposite of a pending one, so nothing is sent). `isPending(id)` says whether a write with that id
 * is queued or still settling, so a repeat press can stay silent instead of saying "already logged".
 */
// eslint-disable-next-line react-refresh/only-export-components
export function usePendingControls(): {
  undoLast: () => boolean;
  flushPending: () => Promise<void>;
  cancel: (id: string) => boolean;
  isPending: (id: string) => boolean;
} {
  const { store } = usePendingContext();
  return useMemo(
    () => ({
      undoLast: () => store.undoLast(),
      flushPending: () => store.flush(),
      cancel: (id: string) => store.cancel(id),
      isPending: (id: string) => store.isPending(id),
    }),
    [store],
  );
}

/** Every view reads through this, never `useQuery` directly, so pending patches show on every result. */
// eslint-disable-next-line react-refresh/only-export-components
export function useViewQuery<T>(
  key: readonly unknown[],
  fetch: () => Promise<T>,
  opts: { pollMs?: number } = {},
): UseQueryResult<T> {
  const { store } = usePendingContext();
  const overlays = useSyncExternalStore(store.subscribe, store.snapshot);
  const hash = hashKey(key);
  const select = useMemo(() => {
    const mine = overlays.filter((o) => o.hash === hash);
    return (data: T): T => mine.reduce<T>((d, o) => o.apply(d) as T, data);
  }, [overlays, hash]);
  return useQuery({ queryKey: key, queryFn: () => fetch(), refetchInterval: opts.pollMs, select });
}
