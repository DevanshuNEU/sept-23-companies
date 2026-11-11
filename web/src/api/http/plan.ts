import type { LiveMethods } from './client';
import { getJson, sendJson } from './api';
import type { Transport } from './transport';

/**
 * The people plan (`GET /api/plan`): today's rows with the state, reason, note and search link the API derives, the
 * LinkedIn month, tomorrow's line and the week's counts. The page computes none of them.
 *
 * The writes log what he did by hand on a row (a step, or the people he asked at a company, pasted), keep his saved
 * people search and his notes; nothing here reaches LinkedIn. Each row write
 * carries the row's `revision` and answers with the row as the plan now shows it. `idempotencyKey` is the touch's own
 * key (`plan:<entryId>:request`, `plan:<entryId>:apply`): the same key twice makes one touch, whatever the request id.
 * A note write answers with the note; the reason line is confirmed only by `confirmReason`.
 */
export const planMethods = (t: Transport): LiveMethods => ({
  plan: () => getJson(t, '/api/plan'),
  logRequest: (entryId, input, revision, o) =>
    sendJson(t, 'post', '/api/plan/{entry_id}/request', { ...input, revision }, o, { params: { entry_id: entryId } }),
  logApplied: (entryId, input, revision, o) =>
    sendJson(t, 'post', '/api/plan/{entry_id}/apply', { ...input, revision }, o, { params: { entry_id: entryId } }),
  logPeople: (entryId, input, revision, o) =>
    sendJson(t, 'post', '/api/plan/{entry_id}/people', { ...input, revision }, o, { params: { entry_id: entryId } }),
  saveSearch: (entryId, url, o) =>
    sendJson(t, 'post', '/api/plan/{entry_id}/search', { url }, o, { params: { entry_id: entryId } }),
  skipRow: (entryId, revision, o) =>
    sendJson(t, 'post', '/api/plan/{entry_id}/skip', { revision }, o, { params: { entry_id: entryId } }),
  passPick: (entryId, input, revision, o) =>
    sendJson(t, 'post', '/api/plan/{entry_id}/pass', { ...input, revision }, o, { params: { entry_id: entryId } }),
  bench: () => getJson(t, '/api/plan/bench'),
  addFromBench: (companyId, input, o) =>
    sendJson(t, 'post', '/api/plan/bench/{company_id}/add', input, o, { params: { company_id: companyId } }),
  browse: (p) =>
    getJson(t, '/api/plan/browse', { query: { q: p.q || undefined, levelRole: p.levelRole ? 'true' : undefined, visaOk: p.visaOk ? 'true' : undefined, unworked: p.unworked ? 'true' : undefined, after: p.after || undefined, limit: p.limit } }),
  jobs: (p) =>
    getJson(t, '/api/jobs', { query: { view: p.view, window: p.window, size: p.size, notContacted: p.notContacted ? 'true' : undefined, q: p.q || undefined, after: p.after || undefined, limit: p.limit } }),
  passJob: (postingId, o) => sendJson(t, 'post', '/api/jobs/{posting_id}/pass', {}, o, { params: { posting_id: postingId } }),
  markApplied: (postingId, o) => sendJson(t, 'post', '/api/jobs/{posting_id}/applied', {}, o, { params: { posting_id: postingId } }),
  applied: () => getJson(t, '/api/applied'),
  markReached: (postingId, o) => sendJson(t, 'post', '/api/jobs/{posting_id}/done', {}, o, { params: { posting_id: postingId } }),
  checkNow: (o) => sendJson(t, 'post', '/api/jobs/check-now', {}, o),
  checkStatus: () => getJson(t, '/api/jobs/check'),
  swapPick: (entryId, input, revision, o) =>
    sendJson(t, 'post', '/api/plan/{entry_id}/swap', { ...input, revision }, o, { params: { entry_id: entryId } }),
  saveNote: (noteId, edit, o) =>
    sendJson(t, 'put', '/api/notes/{note_id}', edit, o, { params: { note_id: noteId } }),
  confirmReason: (noteId, revision, o) =>
    sendJson(t, 'post', '/api/notes/{note_id}/confirm', { revision }, o, { params: { note_id: noteId } }),
});
