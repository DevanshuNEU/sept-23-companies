import type { OutreachClient } from './client';
import type { Scenario } from './fixture/data';

export type * from './types';
export type { OutreachClient, WriteOptions, ApiErrorCode } from './client';
export { ApiError } from './client';
export { ClientProvider, useClient } from './provider';
export { queryKeys, pollMs, newRequestId } from './keys';

/**
 * Phase 1 has no backend: the client is a FixtureClient for the `?scenario=` value, with 150 ms of latency so loading
 * states show. The client and its data are a chunk of their own, loaded here, so the main bundle does not carry them.
 */
export async function createClient(search: string = location.search): Promise<OutreachClient> {
  const [{ FixtureClient }, { buildFixture, SCENARIOS }] = await Promise.all([import('./fixture/client'), import('./fixture/data')]);
  const asked = new URLSearchParams(search).get('scenario');
  const scenario: Scenario = SCENARIOS.find((s) => s === asked) ?? 'default';
  return new FixtureClient(buildFixture(scenario), { latencyMs: 150 });
}
