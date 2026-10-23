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
});
