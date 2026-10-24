import type { LiveMethods } from './client';
import { getJson, sendJson } from './api';
import type { Transport } from './transport';

/**
 * The LinkedIn list (`GET /api/linkedin`), logging a step he took by hand and taking the newest one back. Nothing
 * here reaches LinkedIn: the API only records what he did. A write answers with the person as the list shows them.
 */
export const linkedinMethods = (t: Transport): LiveMethods => ({
  linkedin: () => getJson(t, '/api/linkedin'),
  logLinkedIn: (personId, step, o, replyKind) =>
    sendJson(t, 'post', '/api/linkedin/{person_id}/log', replyKind ? { step, replyKind } : { step }, o, {
      params: { person_id: personId },
    }),
  undoLinkedIn: (personId, step, o) =>
    sendJson(t, 'post', '/api/linkedin/{person_id}/undo', { step }, o, { params: { person_id: personId } }),
});
