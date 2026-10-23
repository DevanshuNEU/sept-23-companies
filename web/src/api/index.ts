import type { OutreachClient } from './client';
import type { Scenario } from './fixture/data';
import { composeClient, parseLiveViews } from './compose';
import { createHttpClient } from './http/client';
import { ALL_VIEWS, DEFAULT_LIVE_VIEWS } from './http/views';

export type * from './types';
export type { OutreachClient, WriteOptions, ApiErrorCode } from './client';
export { ApiError } from './client';
export { ClientProvider, useClient } from './provider';
export { queryKeys, pollMs, newRequestId } from './keys';

/**
 * Each view is served by the local API or by sample data, per `?live=` (a list, `all` or `none`), else
 * `VITE_LIVE_VIEWS`, else `DEFAULT_LIVE_VIEWS`. Sample data is a `FixtureClient` for the `?scenario=` value, with
 * 150 ms of latency so loading states show. The fixtures are a chunk of their own, loaded only when some view is
 * not live, so a fully live page does not carry them.
 */
export async function createClient(search: string = location.search): Promise<OutreachClient> {
  const views = parseLiveViews(search, import.meta.env.VITE_LIVE_VIEWS as string | undefined, DEFAULT_LIVE_VIEWS);
  const live = createHttpClient();
  if (ALL_VIEWS.every((v) => views.includes(v))) return composeClient({ live, sample: null, views });
  const sample = await sampleClient(search);
  return views.length === 0 ? sample : composeClient({ live, sample, views });
}

async function sampleClient(search: string): Promise<OutreachClient> {
  const [{ FixtureClient }, { buildFixture, SCENARIOS }] = await Promise.all([import('./fixture/client'), import('./fixture/data')]);
  const asked = new URLSearchParams(search).get('scenario');
  const scenario: Scenario = SCENARIOS.find((s) => s === asked) ?? 'default';
  return new FixtureClient(buildFixture(scenario), { latencyMs: 150 });
}
