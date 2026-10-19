import type { OutreachClient } from './client';

export type * from './types';
export type { OutreachClient, WriteOptions, ApiErrorCode } from './client';
export { ApiError } from './client';
export { ClientProvider, useClient } from './provider';
export { queryKeys, pollMs, newRequestId } from './keys';

// Phase 1 returns a FixtureClient for the ?scenario= value; Task 4 writes it.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function createClient(_search: string = location.search): OutreachClient {
  throw new Error('FixtureClient arrives in Task 4');
}
