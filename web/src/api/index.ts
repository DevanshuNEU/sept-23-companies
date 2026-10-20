import type { OutreachClient } from './client';
import { FixtureClient } from './fixture/client';
import { buildFixture, SCENARIOS, type Scenario } from './fixture/data';

export type * from './types';
export type { OutreachClient, WriteOptions, ApiErrorCode } from './client';
export { ApiError } from './client';
export { ClientProvider, useClient } from './provider';
export { queryKeys, pollMs, newRequestId } from './keys';

/** Phase 1 has no backend: the client is a FixtureClient for the `?scenario=` value, with 150 ms of latency so loading states show. */
export function createClient(search: string = location.search): OutreachClient {
  const asked = new URLSearchParams(search).get('scenario');
  const scenario: Scenario = SCENARIOS.find((s) => s === asked) ?? 'default';
  return new FixtureClient(buildFixture(scenario), { latencyMs: 150 });
}
