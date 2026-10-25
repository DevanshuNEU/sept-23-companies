import type { LiveMethods } from './client';
import { getJson, sendJson } from './api';
import type { Transport } from './transport';

/**
 * The people plan (`GET /api/plan`): today's rows with the state, reason, note and search link the API derives, the
 * LinkedIn month, tomorrow's line and the week's counts. The page computes none of them.
 *
 * The writes log what he did by hand on a row and keep his notes; nothing here reaches LinkedIn. Each row write
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
  skipRow: (entryId, revision, o) =>
    sendJson(t, 'post', '/api/plan/{entry_id}/skip', { revision }, o, { params: { entry_id: entryId } }),
  saveNote: (noteId, edit, o) =>
    sendJson(t, 'put', '/api/notes/{note_id}', edit, o, { params: { note_id: noteId } }),
  confirmReason: (noteId, revision, o) =>
    sendJson(t, 'post', '/api/notes/{note_id}/confirm', { revision }, o, { params: { note_id: noteId } }),
});
