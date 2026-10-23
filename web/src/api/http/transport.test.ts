import { ApiError } from '../client';
import { createTransport } from './transport';

type Call = { url: string; init: RequestInit };

/** A fake fetch that answers each call with the next response and records what it was asked. */
function fake(...answers: (Response | Error)[]) {
  const calls: Call[] = [];
  const fetchFn = vi.fn(async (url: RequestInfo | URL, init: RequestInit = {}) => {
    calls.push({ url: String(url), init });
    const next = answers.shift() ?? new Response('{}', { status: 200 });
    if (next instanceof Error) throw next;
    return next;
  });
  return { fetchFn, calls };
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const envelope = (code: string, message: string, status: number, checks: unknown[] = []) => json({ code, message, checks }, status);
const headersOf = (init: RequestInit) => Object.fromEntries(Object.entries((init.headers ?? {}) as Record<string, string>).map(([k, v]) => [k.toLowerCase(), v]));
const caught = async (p: Promise<unknown>): Promise<ApiError> => {
  try {
    await p;
  } catch (e) {
    expect(e).toBeInstanceOf(ApiError);
    return e as ApiError;
  }
  throw new Error('expected the call to fail');
};
const WRITE = { requestId: 'req-12345678' };

describe('transport writes', () => {
  it('a write sends Idempotency-Key from requestId and keepalive when asked', async () => {
    const { fetchFn, calls } = fake(json({ ok: true }));
    const t = createTransport({ fetchFn });
    await expect(t.send('POST', '/api/x', { a: 1 }, { requestId: 'req-12345678', keepalive: true })).resolves.toEqual({ ok: true });
    const { url, init } = calls[0]!;
    expect(url).toBe('/api/x');
    expect(init.method).toBe('POST');
    expect(init.body).toBe('{"a":1}');
    expect(init.keepalive).toBe(true);
    expect(headersOf(init)).toMatchObject({ 'content-type': 'application/json', 'idempotency-key': 'req-12345678' });
  });

  it('keepalive is false unless asked, and the base is prefixed', async () => {
    const { fetchFn, calls } = fake(json({}));
    await createTransport({ fetchFn, base: 'http://127.0.0.1:9' }).send('PUT', '/api/x', {}, WRITE);
    expect(calls[0]!.url).toBe('http://127.0.0.1:9/api/x');
    expect(calls[0]!.init.keepalive).toBe(false);
  });

  it('the transport never sets an Origin header', async () => {
    const { fetchFn, calls } = fake(json({}), json({}));
    const t = createTransport({ fetchFn });
    await t.send('POST', '/api/x', {}, WRITE);
    await t.get('/api/x');
    for (const c of calls) expect(Object.keys(headersOf(c.init))).not.toContain('origin');
  });

  it('a write with no content still carries the JSON content type and reads an empty 204', async () => {
    const { fetchFn, calls } = fake(new Response(null, { status: 204 }));
    await expect(createTransport({ fetchFn }).send('DELETE', '/api/x', undefined, WRITE)).resolves.toBeUndefined();
    expect(headersOf(calls[0]!.init)['content-type']).toBe('application/json');
    expect(calls[0]!.init.body).toBeUndefined();
  });
});

describe('transport reads', () => {
  it('a read sends no body, no-store and no Idempotency-Key', async () => {
    const { fetchFn, calls } = fake(json({ n: 1 }));
    await expect(createTransport({ fetchFn }).get('/api/x')).resolves.toEqual({ n: 1 });
    const { init } = calls[0]!;
    expect(init.method ?? 'GET').toBe('GET');
    expect(init.body).toBeUndefined();
    expect(init.cache).toBe('no-store');
    expect(Object.keys(headersOf(init))).not.toContain('idempotency-key');
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it('a read times out after 15 seconds and says so as a network error', async () => {
    vi.useFakeTimers();
    try {
      const fetchFn = vi.fn((_url: RequestInfo | URL, init: RequestInit = {}) => new Promise<Response>((_resolve, reject) => init.signal?.addEventListener('abort', () => reject(init.signal?.reason))));
      const failed = caught(createTransport({ fetchFn }).get('/api/x'));
      await vi.advanceTimersByTimeAsync(14999);
      expect(fetchFn).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(2);
      expect((await failed).code).toBe('network');
    } finally {
      vi.useRealTimers();
    }
  });

  it("the caller's signal cancels a read", async () => {
    const fetchFn = vi.fn((_url: RequestInfo | URL, init: RequestInit = {}) => new Promise<Response>((_resolve, reject) => init.signal?.addEventListener('abort', () => reject(init.signal?.reason))));
    const stop = new AbortController();
    const failed = caught(createTransport({ fetchFn }).get('/api/x', stop.signal));
    stop.abort();
    expect((await failed).code).toBe('network');
  });
});

describe('transport errors', () => {
  it('409 with the envelope becomes ApiError conflict', async () => {
    const { fetchFn } = fake(envelope('conflict', 'This email changed since you loaded it', 409));
    const e = await caught(createTransport({ fetchFn }).send('POST', '/api/x', {}, WRITE));
    expect(e.code).toBe('conflict');
    expect(e.message).toBe('This email changed since you loaded it');
  });

  it('422 blocked carries its checks', async () => {
    const check = { code: 'near_identical', title: 'Near-identical', detail: 'Too close to another draft', severity: 'block', spans: [], fix: null };
    const { fetchFn } = fake(envelope('blocked', 'Blocked', 422, [check]));
    const e = await caught(createTransport({ fetchFn }).send('POST', '/api/x', {}, WRITE));
    expect(e.code).toBe('blocked');
    expect(e.checks).toEqual([check]);
  });

  it('capacity and not_allowed keep their code', async () => {
    const { fetchFn } = fake(envelope('capacity', 'Today is full', 409), envelope('not_allowed', 'Already claimed', 409));
    const t = createTransport({ fetchFn });
    expect((await caught(t.send('POST', '/api/x', {}, WRITE))).code).toBe('capacity');
    expect((await caught(t.send('POST', '/api/x', {}, WRITE))).code).toBe('not_allowed');
  });

  it('404 becomes not_found', async () => {
    const { fetchFn } = fake(envelope('not_found', 'No such company', 404));
    expect((await caught(createTransport({ fetchFn }).get('/api/x'))).code).toBe('not_found');
  });

  it("403 forbidden becomes not_allowed with the API's message", async () => {
    const { fetchFn } = fake(envelope('forbidden', 'This page may not change the store', 403));
    const e = await caught(createTransport({ fetchFn }).send('POST', '/api/x', {}, WRITE));
    expect(e.code).toBe('not_allowed');
    expect(e.message).toBe('This page may not change the store');
  });

  it('bad_request, idempotency_mismatch and server_error become not_allowed with the message', async () => {
    const { fetchFn } = fake(
      envelope('bad_request', 'The request was not valid', 400),
      envelope('idempotency_mismatch', 'That key was used for another change', 422),
      envelope('server_error', 'The server had a problem. Nothing was saved.', 500),
    );
    const t = createTransport({ fetchFn });
    const a = await caught(t.send('POST', '/api/x', {}, WRITE));
    const b = await caught(t.send('POST', '/api/x', {}, WRITE));
    const c = await caught(t.send('POST', '/api/x', {}, WRITE));
    expect([a.code, b.code, c.code]).toEqual(['not_allowed', 'not_allowed', 'not_allowed']);
    expect([a.message, b.message, c.message]).toEqual(['The request was not valid', 'That key was used for another change', 'The server had a problem. Nothing was saved.']);
  });

  it('a thrown fetch becomes network', async () => {
    const { fetchFn } = fake(new TypeError('Failed to fetch'), new TypeError('Failed to fetch'));
    const t = createTransport({ fetchFn });
    expect((await caught(t.get('/api/x'))).code).toBe('network');
    expect((await caught(t.send('POST', '/api/x', {}, WRITE))).code).toBe('network');
  });

  it('a 500 with an HTML body becomes network, not a JSON parse error', async () => {
    const { fetchFn } = fake(new Response('<html><body>Bad gateway</body></html>', { status: 502, headers: { 'Content-Type': 'text/html' } }));
    const e = await caught(createTransport({ fetchFn }).get('/api/x'));
    expect(e.code).toBe('network');
    expect(e.message).not.toMatch(/JSON|Unexpected token/);
  });

  it('a 200 whose body is not JSON becomes network', async () => {
    const { fetchFn } = fake(new Response('<html></html>', { status: 200 }));
    expect((await caught(createTransport({ fetchFn }).get('/api/x'))).code).toBe('network');
  });

  it('503 busy says nothing was saved', async () => {
    const { fetchFn } = fake(envelope('busy', 'The store is busy right now. Nothing was saved. Try again in a moment.', 503));
    const e = await caught(createTransport({ fetchFn }).send('POST', '/api/x', {}, WRITE));
    expect(e.code).toBe('network');
    expect(e.message).toBe('The server is busy. Nothing was saved. Try again.');
  });
});
