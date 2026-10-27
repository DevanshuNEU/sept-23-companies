import { ApiError, type OutreachClient } from '../client';
import type { NoteDraft, PlanRow, PlanView } from '../types';
import { createHttpClient } from './client';
import { createTransport } from './transport';

/**
 * The plan's methods end to end through the real transport, with a fake fetch: the path, the body, the
 * `Idempotency-Key` header, and how each of the API's answers reaches the page. Nothing here reaches a server.
 */

type Call = { url: string; init: RequestInit };

function fake(...answers: Response[]) {
  const calls: Call[] = [];
  const fetchFn = vi.fn(async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return answers.shift() ?? new Response('{}', { status: 200 });
  });
  const client = createHttpClient(createTransport({ fetchFn })) as unknown as OutreachClient;
  return { client, calls };
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const envelope = (code: string, message: string, status: number) => json({ code, message, checks: [] }, status);
const headersOf = (init: RequestInit) => Object.fromEntries(Object.entries((init.headers ?? {}) as Record<string, string>).map(([k, v]) => [k.toLowerCase(), v]));
const bodyOf = (init: RequestInit) => JSON.parse(String(init.body)) as unknown;
const caught = async (p: Promise<unknown>): Promise<ApiError> => {
  try {
    await p;
  } catch (e) {
    expect(e).toBeInstanceOf(ApiError);
    return e as ApiError;
  }
  throw new Error('expected the call to fail');
};

const o = { requestId: 'req-plan-0001' };

const NOT_BUILT: PlanView = {
  asOf: '2026-11-10T13:00:00Z', date: '2026-11-10', builtAt: null, listJob: null,
  notReady: { reason: 'not_built', text: "Today's plan is not built yet. It builds at 8:00 on weekdays." },
  rows: [], done: 0, total: 0,
  linkedinWeek: { used: 0, limit: 150, hardUsed: 0, hardLimit: 200 },
  tomorrow: { total: 7, text: 'Tomorrow: 7 people.', preview: [] },
  week: { requests: 0, accepts: 0, replies: 0 },
  inFlight: [],
  passed: 0,
};

/** Just enough of a row and a note: the client hands back what the API sent, field for field. */
const ROW = { entryId: 31, state: 'todo', revision: 3, steps: [{ kind: 'request', touchId: 900, at: '2026-11-10T14:00:00Z' }] } as unknown as PlanRow;
const NOTE = { noteId: 7, revision: 4, reasonState: 'written' } as unknown as NoteDraft;

/** Each plan write: how to call it, where it goes and with what body, and what it answers. */
const WRITES: {
  name: string;
  call: (c: OutreachClient) => Promise<unknown>;
  method: string;
  url: string;
  body: unknown;
  answer: unknown;
}[] = [
  {
    name: 'logRequest',
    call: (c) => c.logRequest(31, { personId: 4, idempotencyKey: 'plan:31:request' }, 2, o),
    method: 'POST', url: '/api/plan/31/request', body: { personId: 4, idempotencyKey: 'plan:31:request', revision: 2 }, answer: ROW,
  },
  {
    name: 'logApplied',
    call: (c) => c.logApplied(31, { postingId: 9, idempotencyKey: 'plan:31:apply' }, 2, o),
    method: 'POST', url: '/api/plan/31/apply', body: { postingId: 9, idempotencyKey: 'plan:31:apply', revision: 2 }, answer: ROW,
  },
  { name: 'skipRow', call: (c) => c.skipRow(31, 2, o), method: 'POST', url: '/api/plan/31/skip', body: { revision: 2 }, answer: ROW },
  {
    name: 'saveNote',
    call: (c) => c.saveNote(7, { text: 'Hi Lena.', reasonLine: 'your post on retries', revision: 3 }, o),
    method: 'PUT', url: '/api/notes/7', body: { text: 'Hi Lena.', reasonLine: 'your post on retries', revision: 3 }, answer: NOTE,
  },
  { name: 'confirmReason', call: (c) => c.confirmReason(7, 3, o), method: 'POST', url: '/api/notes/7/confirm', body: { revision: 3 }, answer: NOTE },
];

describe('plan through the transport', () => {
  it('plan() reads /api/plan uncached and hands back a not-built day as it came', async () => {
    const { client, calls } = fake(json(NOT_BUILT));
    expect(await client.plan()).toEqual(NOT_BUILT);
    expect(calls[0]?.url).toBe('/api/plan');
    expect(calls[0]?.init).toMatchObject({ method: 'GET', cache: 'no-store' });
  });

  it('plan() maps a server failure to network, with nothing said about saving', async () => {
    const { client } = fake(new Response('<html>Bad gateway</html>', { status: 502 }));
    const err = await caught(client.plan());
    expect(err.code).toBe('network');
    expect(err.message).toBe('The server had a problem. Try again.');
  });

  for (const w of WRITES) {
    describe(w.name, () => {
      it('sends the body with the revision, keyed by the request id, and answers with what the API sent', async () => {
        const { client, calls } = fake(json(w.answer));
        expect(await w.call(client)).toEqual(w.answer);
        const call = calls[0];
        if (!call) throw new Error('no call');
        expect(call.url).toBe(w.url);
        expect(call.init.method).toBe(w.method);
        expect(bodyOf(call.init)).toEqual(w.body);
        expect(headersOf(call.init)).toMatchObject({ 'content-type': 'application/json', 'idempotency-key': o.requestId });
      });

      it('a stale revision is the API conflict, with its message kept', async () => {
        const message = 'The plan row changed since you loaded it. Nothing was saved.';
        const { client } = fake(envelope('conflict', message, 409));
        const err = await caught(w.call(client));
        expect(err.code).toBe('conflict');
        expect(err.message).toBe(message);
      });

      it('a retry of the same press is the same change: same key and body, and the replayed answer comes back', async () => {
        const { client, calls } = fake(json(w.answer), json(w.answer));
        const first = await w.call(client);
        const again = await w.call(client);
        expect(again).toEqual(first);
        expect(calls).toHaveLength(2);
        expect(headersOf(calls[1]!.init)['idempotency-key']).toBe(headersOf(calls[0]!.init)['idempotency-key']);
        expect(calls[1]!.init.body).toBe(calls[0]!.init.body);
      });

      it('not_allowed keeps its code and the API message', async () => {
        const message = 'This row is done, so it stays on today\'s plan. Nothing was saved.';
        const { client } = fake(envelope('not_allowed', message, 409));
        const err = await caught(w.call(client));
        expect(err.code).toBe('not_allowed');
        expect(err.message).toBe(message);
      });

      it('bad_request reads as not_allowed with the API message', async () => {
        const message = 'That key belongs to another touch, not this row\'s request. Nothing was saved.';
        const { client } = fake(envelope('bad_request', message, 400));
        const err = await caught(w.call(client));
        expect(err.code).toBe('not_allowed');
        expect(err.message).toBe(message);
      });

      it('an unknown row or note is not_found', async () => {
        const { client } = fake(envelope('not_found', 'There is no plan row 31.', 404));
        expect((await caught(w.call(client))).code).toBe('not_found');
      });
    });
  }

  it('a log after a lost answer keeps the touch key, so a new request id still makes one touch', async () => {
    // The page sends the touch's own key (`plan:<entryId>:request`) in the body: a second press with a new request
    // id finds the touch the first one made, and the API answers with the row, writing nothing.
    const { client, calls } = fake(json(ROW), json(ROW));
    await client.logRequest(31, { personId: 4, idempotencyKey: 'plan:31:request' }, 2, { requestId: 'req-a' });
    await client.logRequest(31, { personId: 4, idempotencyKey: 'plan:31:request' }, 2, { requestId: 'req-b' });
    expect(calls.map((c) => headersOf(c.init)['idempotency-key'])).toEqual(['req-a', 'req-b']);
    expect(calls.map((c) => (bodyOf(c.init) as { idempotencyKey: string }).idempotencyKey)).toEqual(['plan:31:request', 'plan:31:request']);
  });

  it('a write that goes out as the tab closes is sent with keepalive', async () => {
    const { client, calls } = fake(json(ROW));
    await client.skipRow(31, 2, { requestId: 'req-close', keepalive: true });
    expect(calls[0]?.init.keepalive).toBe(true);
  });
});
