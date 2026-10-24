import type { OutreachClient } from './client';
import { clientForPage, composeClient } from './compose';
import { ALL_VIEWS, VIEW_OF_METHOD, type ViewName } from './http/views';

const loaded = vi.hoisted(() => ({ client: 0, data: 0 }));

type Method = keyof typeof VIEW_OF_METHOD;
const METHODS = Object.keys(VIEW_OF_METHOD) as Method[];

/** A client whose every method answers with its own label, so a test sees which side was called. */
function labelled(label: string, source: 'sample' | 'live'): OutreachClient {
  return { source, ...Object.fromEntries(METHODS.map((m) => [m, vi.fn(async () => label)])) } as unknown as OutreachClient;
}

// Compile only: every method of the interface has a view, and the map has nothing else.
type Covered = Exclude<keyof OutreachClient, 'source'>;
export const _covers = VIEW_OF_METHOD satisfies Record<Covered, ViewName>;
export const _noExtras: Covered[] = Object.keys(VIEW_OF_METHOD) as Method[];

describe('composeClient', () => {
  it('a live view calls live and a sample view calls sample', async () => {
    const live = labelled('live', 'live');
    const sample = labelled('sample', 'sample');
    const c = composeClient({ live, sample, views: ['outbox'] });
    expect(await c.outbox()).toBe('live');
    expect(await c.approve(1, 1, { requestId: 'r' })).toBe('live');
    expect(await c.shell()).toBe('sample');
    expect(await c.today()).toBe('sample');
    expect(live.shell).not.toHaveBeenCalled();
    expect(sample.outbox).not.toHaveBeenCalled();
  });

  it('passes arguments through unchanged', async () => {
    const live = labelled('live', 'live');
    const c = composeClient({ live, sample: labelled('sample', 'sample'), views: ['outbox'] });
    await c.skip(7, 3, { requestId: 'r', keepalive: true });
    expect(live.skip).toHaveBeenCalledWith(7, 3, { requestId: 'r', keepalive: true });
  });

  it('source is sample until every view is live', () => {
    const live = labelled('live', 'live');
    const sample = labelled('sample', 'sample');
    expect(composeClient({ live, sample, views: [] }).source).toBe('sample');
    expect(composeClient({ live, sample, views: ALL_VIEWS.filter((v) => v !== 'wins') }).source).toBe('sample');
    expect(composeClient({ live, sample, views: ALL_VIEWS }).source).toBe('live');
  });

  it('a method of a view that is not live yet fails loudly instead of returning nothing', () => {
    const live = { source: 'live' } as unknown as OutreachClient;
    const c = composeClient({ live, sample: labelled('sample', 'sample'), views: ['outbox'] });
    expect(() => c.outbox()).toThrow(/outbox/);
  });
});

describe('createClient', () => {
  beforeEach(() => {
    loaded.client = 0;
    loaded.data = 0;
    vi.resetModules();
    vi.unstubAllEnvs();
  });
  afterEach(() => vi.unstubAllEnvs());

  // A factory runs when its module is first imported after a reset, so a count says whether the chunk was imported.
  async function make(search: string) {
    vi.doMock('./fixture/client', () => {
      loaded.client += 1;
      return { FixtureClient: class { readonly source = 'sample' as const } };
    });
    vi.doMock('./fixture/data', () => {
      loaded.data += 1;
      return { SCENARIOS: ['default'], buildFixture: () => ({}) };
    });
    const { createClient } = await import('./index');
    return createClient(search);
  }

  it('?live=none picks no view and keeps the sample client as it is', async () => {
    const c = await make('?live=none');
    expect(c.source).toBe('sample');
    expect(loaded.client).toBe(1);
  });

  it('?live=all picks every view, and the fixture chunk is not imported', async () => {
    const c = await make('?live=all');
    expect(c.source).toBe('live');
    expect(loaded.client).toBe(0);
    expect(loaded.data).toBe(0);
  });

  it('?live=plan,outbox picks those views and loads the fixtures for the rest', async () => {
    const c = await make('?live=plan,outbox');
    expect(c.source).toBe('sample');
    expect(loaded.client).toBe(1);
  });

  it('an unknown view name is ignored', async () => {
    const { parseLiveViews } = await import('./compose');
    expect(parseLiveViews('?live=plan,nope,outbox', undefined, [])).toEqual(['plan', 'outbox']);
    expect(parseLiveViews('?live=nope', undefined, ['today'])).toEqual([]);
    expect(parseLiveViews('?live=all', undefined, [])).toEqual(ALL_VIEWS);
    expect(parseLiveViews('?live=none', 'all', ['today'])).toEqual([]);
  });

  it('falls back to VITE_LIVE_VIEWS, then to the defaults', async () => {
    const { parseLiveViews } = await import('./compose');
    expect(parseLiveViews('', 'wins, watch', ['today'])).toEqual(['wins', 'watch']);
    expect(parseLiveViews('?scenario=default', undefined, ['today'])).toEqual(['today']);
    expect(parseLiveViews('?live=plan', 'all', ['today'])).toEqual(['plan']);
  });

  it('an env of all makes the whole client live', async () => {
    vi.stubEnv('VITE_LIVE_VIEWS', 'all');
    expect((await make('')).source).toBe('live');
    expect(loaded.client).toBe(0);
  });
});

describe('clientForPage', () => {
  it('a page on sample data sends every method to sample, its writes to a live view too', async () => {
    const live = labelled('live', 'live');
    const sample = labelled('sample', 'sample');
    const c = composeClient({ live, sample, views: ['company'] });
    const outboxPage = clientForPage(c, 'outbox');
    expect(await outboxPage.pauseCompany(1, 1, { requestId: 'r' })).toBe('sample');
    expect(await outboxPage.company(1)).toBe('sample');
    expect(live.pauseCompany).not.toHaveBeenCalled();
  });

  it('a live page keeps the composed client, and a plain client is its own scope', async () => {
    const live = labelled('live', 'live');
    const sample = labelled('sample', 'sample');
    const c = composeClient({ live, sample, views: ['company'] });
    expect(clientForPage(c, 'company')).toBe(c);
    expect(await clientForPage(c, 'company').outbox()).toBe('sample');
    expect(clientForPage(sample, 'outbox')).toBe(sample);
  });
});
