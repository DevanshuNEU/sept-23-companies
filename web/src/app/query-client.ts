import { QueryClient } from '@tanstack/react-query';
import { ApiError } from '@/api';

/**
 * A read that got no answer (offline, timed out, the server busy or failing) gets one more try. Any answer from the
 * server (blocked, conflict, not found) is final. More tries from every open tab are what kept a busy server busy.
 */
function retry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.code !== 'network') return false;
  return failureCount < 1;
}

/** The wait before a retry: 2 seconds, doubling, at most 15, then spread by up to twice that so tabs that failed
 * together do not come back together. */
export function retryDelay(attempt: number, _error: unknown, random: () => number = Math.random): number {
  return Math.min(2000 * 2 ** attempt, 15000) * (1 + random());
}

/**
 * `staleTime` 10 seconds: a view read in the last 10 seconds is not read again when a page remounts it, the tab
 * regains focus or the network comes back, so a reconnect does not ask for every view in every tab at once. A pulse
 * change still refetches at once (`invalidateQueries` ignores `staleTime`), and each view keeps its own poll.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry, retryDelay, staleTime: 10000 } } });
}
