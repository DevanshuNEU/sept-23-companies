import { createHttpClient } from './client';
import { DEFAULT_LIVE_VIEWS, VIEW_OF_METHOD } from './views';
import type { Transport } from './transport';

describe('DEFAULT_LIVE_VIEWS', () => {
  it('a view is live by default only when the HTTP client serves every method of it', () => {
    const live = createHttpClient({ get: vi.fn(), send: vi.fn() } as unknown as Transport) as Record<string, unknown>;
    const missing = Object.entries(VIEW_OF_METHOD)
      .filter(([, view]) => DEFAULT_LIVE_VIEWS.includes(view))
      .filter(([name]) => typeof live[name] !== 'function')
      .map(([name, view]) => `${view}.${name}`);
    expect(missing).toEqual([]);
  });

  it('sending goes live only with the outbox and the shell, whose pages read its status', () => {
    // Its pause and resume are live, but the strip and the palette read the state through the outbox and shell
    // views: with those on sample data, a resume could answer a sample hard block on the real sender.
    if (DEFAULT_LIVE_VIEWS.includes('sending')) expect(DEFAULT_LIVE_VIEWS).toEqual(expect.arrayContaining(['outbox', 'shell']));
  });
});
