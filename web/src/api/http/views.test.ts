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

  it('the outbox, sending and the shell go live together', () => {
    // Pause and resume are live, but the strip and the palette read the sender's state through the outbox and
    // shell views: with either on sample data, a resume could answer a sample hard block on the real sender.
    const together = ['outbox', 'sending', 'shell'] as const;
    const on = together.filter((v) => DEFAULT_LIVE_VIEWS.includes(v));
    expect(on.length === 0 || on.length === together.length).toBe(true);
  });
});
