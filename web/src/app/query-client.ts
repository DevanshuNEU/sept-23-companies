import { QueryClient } from '@tanstack/react-query';
import { ApiError } from '@/api';

/** A network failure gets two more tries. Any answer from the server (blocked, conflict, not found) is final. */
function retry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.code !== 'network') return false;
  return failureCount < 2;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry } } });
}
