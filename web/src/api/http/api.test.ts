import type { components } from '../schema';
import { getJson, type Ok } from './api';
import type { Transport } from './transport';

// Compile only: the helper's return type is the generated model, not a hand copy.
type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : never) : never;
export const _pingIsGenerated: Same<Ok<'/api/ping', 'get'>, components['schemas']['Ping']> = true;

describe('getJson', () => {
  it('reads the path through the transport and passes the signal', async () => {
    const get = vi.fn(async () => ({ ok: true }));
    const t = { get, send: vi.fn() } as unknown as Transport;
    const stop = new AbortController();
    await getJson(t, '/api/ping', { signal: stop.signal });
    expect(get).toHaveBeenCalledWith('/api/ping', stop.signal);
  });
});
