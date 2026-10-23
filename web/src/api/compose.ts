import type { OutreachClient } from './client';
import type { LiveMethods } from './http/client';
import { ALL_VIEWS, VIEW_OF_METHOD, type ViewName } from './http/views';

type MethodName = keyof typeof VIEW_OF_METHOD;

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
