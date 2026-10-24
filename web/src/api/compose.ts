import type { OutreachClient } from './client';
import type { LiveMethods } from './http/client';
import { ALL_VIEWS, VIEW_OF_METHOD, type ViewName } from './http/views';

type MethodName = keyof typeof VIEW_OF_METHOD;

/** Where a composed client keeps its page scope (see `clientForPage`); not part of `OutreachClient`. */
const PAGE_SCOPE = Symbol('pageScope');
type Scoped = { [PAGE_SCOPE]?: (page: ViewName) => OutreachClient };

/**
 * The client one page talks to. A page whose own view is live gets the composed client. A page on sample data
 * gets the sample client for every method, its writes too: otherwise a sample page could send a fixture id to a
 * live write of another view (the sample Outbox pausing a real company). A plain client (a test's fixture) is
 * its own scope.
 */
export function clientForPage(client: OutreachClient, page: ViewName): OutreachClient {
  return (client as OutreachClient & Scoped)[PAGE_SCOPE]?.(page) ?? client;
}

/**
 * One client from two: each method goes to `live` when its view is in `views`, else to `sample`. `source` is `'live'`
 * only when every view is live, so the "Sample data" badge never hides sample data. A method with no implementation
 * on the side it was sent to throws, so a view switched on before its code exists is loud.
 */
export function composeClient({ live, sample, views }: { live: LiveMethods; sample: LiveMethods | null; views: readonly ViewName[] }): OutreachClient {
  const on = new Set(views);
  const client: Record<string, unknown> = {
    get source() {
      return ALL_VIEWS.every((v) => on.has(v)) ? 'live' : 'sample';
    },
  };
  for (const name of Object.keys(VIEW_OF_METHOD) as MethodName[]) {
    const view = VIEW_OF_METHOD[name];
    client[name] = (...args: unknown[]) => {
      const side = on.has(view) ? live : sample;
      const method = side?.[name] as ((...a: unknown[]) => unknown) | undefined;
      if (!method) throw new Error(`${name} has no ${on.has(view) ? 'live' : 'sample'} implementation for the ${view} view`);
      return method.apply(side, args);
    };
  }
  let sampleOnly: OutreachClient | null = null; // built on first use: built here, it would build its own, forever
  Object.defineProperty(client, PAGE_SCOPE, {
    value: (page: ViewName) => {
      if (on.has(page) || !sample) return client as unknown as OutreachClient;
      return (sampleOnly ??= composeClient({ live, sample, views: [] }));
    },
  });
  return client as unknown as OutreachClient;
}

const isView = (name: string): name is ViewName => (ALL_VIEWS as readonly string[]).includes(name);

function parseList(value: string): ViewName[] {
  const names = value.split(',').map((n) => n.trim());
  if (names.includes('all')) return [...ALL_VIEWS];
  return names.filter(isView);
}

/**
 * Which views are live. `?live=a,b`, `?live=all` or `?live=none` wins; else `VITE_LIVE_VIEWS` (same words); else the
 * defaults. Unknown names are ignored. An empty value counts as not set.
 */
export function parseLiveViews(search: string, env: string | undefined, defaults: readonly ViewName[]): ViewName[] {
  for (const value of [new URLSearchParams(search).get('live'), env]) {
    if (value === null || value === undefined || value.trim() === '') continue;
    return value.trim() === 'none' ? [] : parseList(value);
  }
  return [...defaults];
}
