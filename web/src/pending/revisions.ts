import type { QueryClient } from '@tanstack/react-query';
import type { Revision, WriteOptions } from '@/api';

/**
 * The newest revision this tab has seen per thing (`message:12`, `company:3`), per query client: from reads a
 * write made and from what a commit returned. The query cache catches up only after its refetch, so a write
 * that cannot read first (the tab is closing) takes the newest of these and the cache.
 */
const seen = new WeakMap<QueryClient, Map<string, Revision>>();

export function noteRevision(queryClient: QueryClient, key: string, revision: Revision): void {
  const map = seen.get(queryClient) ?? new Map<string, Revision>();
  map.set(key, Math.max(revision, map.get(key) ?? revision));
  seen.set(queryClient, map);
}

/** The newest of what was noted for `key` and `also` (the revision on screen, the cached one). Undefined when none is known. */
export function newestRevision(queryClient: QueryClient, key: string, ...also: (Revision | undefined)[]): Revision | undefined {
  const known = [seen.get(queryClient)?.get(key), ...also].filter((r): r is Revision => r !== undefined);
  return known.length > 0 ? Math.max(...known) : undefined;
}

/**
 * Sends a write that needs the thing's current revision. Normally it reads the revision first: another write
 * may have moved it while this one waited in its window. On a flush as the tab closes (`keepalive`) a read
 * never comes back, so it sends at once, in the same task, with the newest revision known. Only when none is
 * known does it read anyway.
 */
export function withRevision<T>(
  o: WriteOptions,
  known: () => Revision | undefined,
  read: () => Promise<Revision>,
  send: (revision: Revision) => Promise<T>,
): Promise<T> {
  const now = o.keepalive ? known() : undefined;
  return now !== undefined ? send(now) : read().then(send);
}
