import type { WriteOptions } from '../client';
import { networkError, toApiError, unreadable } from './errors';

export type WriteMethod = 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export interface Transport {
  get<T>(path: string, signal?: AbortSignal): Promise<T>;
  send<T>(method: WriteMethod, path: string, body: unknown, o: WriteOptions): Promise<T>;
}

export type FetchFn = (url: string, init: RequestInit) => Promise<Response>;

const READ_TIMEOUT_MS = 15000;

/** The JSON of a response, `undefined` when it is empty (204) and `null` when it is not JSON. */
async function readJson(res: Response): Promise<unknown> {
  const text = await res.text();
  if (text === '') return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/**
 * The one place the page talks to the API. It never sets `Origin`: the browser adds the page's own, which is what
 * the API's guards check. A write carries `Idempotency-Key` (the write's `requestId`), so a retry of the same click
 * is the same change; `keepalive` lets one started as the tab closes finish.
 */
export function createTransport({ fetchFn = (url, init) => fetch(url, init), base = '' }: { fetchFn?: FetchFn; base?: string } = {}): Transport {
  async function call<T>(url: string, init: RequestInit, write: boolean, done: () => void = () => undefined): Promise<T> {
    let res: Response;
    let body: unknown;
    try {
      res = await fetchFn(url, init);
      body = await readJson(res);
    } catch {
      throw networkError(write);
    } finally {
      done();
    }
    if (!res.ok) throw toApiError(res.status, body ?? undefined, write);
    if (body === null) throw unreadable(write);
    return body as T;
  }

  return {
    get<T>(path: string, signal?: AbortSignal): Promise<T> {
      // A timer, not AbortSignal.timeout, so a test can move the clock.
      const timeout = new AbortController();
      const timer = setTimeout(() => timeout.abort(new DOMException('The read took too long', 'TimeoutError')), READ_TIMEOUT_MS);
      const stop = signal ? AbortSignal.any([signal, timeout.signal]) : timeout.signal;
      return call<T>(base + path, { method: 'GET', cache: 'no-store', signal: stop }, false, () => clearTimeout(timer));
    },
    send<T>(method: WriteMethod, path: string, body: unknown, o: WriteOptions): Promise<T> {
      const init: RequestInit = {
        method,
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': o.requestId },
        keepalive: o.keepalive ?? false,
      };
      if (body !== undefined) init.body = JSON.stringify(body);
      return call<T>(base + path, init, true);
    },
  };
}
