import { ApiError, type ApiErrorCode } from '../client';
import type { Check } from '../types';

export const BUSY_MESSAGE = 'The server is busy. Nothing was saved. Try again.';
const UNREACHABLE_READ = 'Could not reach the server. Try again.';
const UNREACHABLE_WRITE = 'Could not reach the server. The change may not have gone through. Try again.';
const PROBLEM = 'The server had a problem. Try again.';
const UNREADABLE = 'The server sent something this page could not read. Try again.';
// A write whose answer was lost may have saved: reloading shows whether it did, so he never repeats it under a new key.
const UNREADABLE_WRITE = 'The server sent an answer this page could not read. The change may have gone through: reload to check before you try again.';
const PROBLEM_WRITE = 'The server had a problem. The change may have gone through: reload to check before you try again.';

/** The API's own codes that the page keeps as they are. */
const KEPT: readonly ApiErrorCode[] = ['blocked', 'conflict', 'capacity', 'not_allowed', 'not_found'];

interface Envelope {
  code: string;
  message: string;
  checks: Check[];
}

function asEnvelope(body: unknown): Envelope | null {
  if (typeof body !== 'object' || body === null) return null;
  const b = body as Record<string, unknown>;
  if (typeof b.code !== 'string' || typeof b.message !== 'string') return null;
  return { code: b.code, message: b.message, checks: Array.isArray(b.checks) ? (b.checks as Check[]) : [] };
}

/** A fetch that threw (offline, refused, timed out, cancelled). A write cannot say it saved nothing: the answer may have been lost. */
export function networkError(write: boolean): ApiError {
  return new ApiError('network', write ? UNREACHABLE_WRITE : UNREACHABLE_READ);
}

/** A 2xx whose body is not JSON. On a write the change may have been saved. */
export function unreadable(write = false): ApiError {
  return new ApiError('network', write ? UNREADABLE_WRITE : UNREADABLE);
}

/**
 * A non-2xx answer as an `ApiError`. `body` is the parsed JSON or `undefined` when it was not JSON (an HTML error page
 * from a proxy). The API's envelope keeps its message; the codes the page has no case for (`forbidden`, `bad_request`,
 * `idempotency_mismatch`, `server_error`) read as `not_allowed`; `busy` and anything unreadable read as `network`. A
 * 5xx that is not the envelope did not come from the API's own error handling, so on a write it may have saved.
 */
export function toApiError(status: number, body: unknown, write = false): ApiError {
  const env = asEnvelope(body);
  if (!env) {
    if (status >= 500) return new ApiError('network', write ? PROBLEM_WRITE : PROBLEM);
    return new ApiError(status === 404 ? 'not_found' : 'not_allowed', 'The server refused the request. Nothing was saved.');
  }
  if (env.code === 'busy') return new ApiError('network', BUSY_MESSAGE);
  const code = KEPT.find((c) => c === env.code) ?? 'not_allowed';
  return new ApiError(code, env.message, code === 'blocked' ? env.checks : []);
}
