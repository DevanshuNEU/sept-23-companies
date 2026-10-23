import type { LiveMethods } from './client';
import { getJson, sendJson } from './api';
import type { Transport } from './transport';

/**
 * The Outbox reads: the list with the sending strip (`GET /api/outbox`), one thread (`GET /api/outbox/{touchId}`)
 * and two drafts at one company side by side (`GET /api/outbox/compare`); and its writes, each with the revision the
 * page loaded and each answering with the thread as it now is. Approve is decided on the server (#9 blocks): a block
 * comes back as `blocked` with the checks.
 */
export const outboxMethods = (t: Transport): LiveMethods => ({
  outbox: () => getJson(t, '/api/outbox'),
  outboxDetail: (touchId) => getJson(t, '/api/outbox/{touch_id}', { params: { touch_id: touchId } }),
  compareDrafts: (messageId, otherMessageId) => getJson(t, '/api/outbox/compare', { query: { messageId, otherMessageId } }),
  saveMessage: (messageId, edit, o) =>
    sendJson(t, 'post', '/api/messages/{message_id}/save', { subject: edit.subject, body: edit.body, revision: edit.revision }, o, { params: { message_id: messageId } }),
  approve: (messageId, revision, o) => sendJson(t, 'post', '/api/messages/{message_id}/approve', { revision }, o, { params: { message_id: messageId } }),
  unapprove: (messageId, revision, o) => sendJson(t, 'post', '/api/messages/{message_id}/unapprove', { revision }, o, { params: { message_id: messageId } }),
  skip: (messageId, revision, o) => sendJson(t, 'post', '/api/messages/{message_id}/skip', { revision }, o, { params: { message_id: messageId } }),
  setReplyKind: (touchId, kind, revision, o) => sendJson(t, 'post', '/api/touches/{touch_id}/reply-kind', { kind, revision }, o, { params: { touch_id: touchId } }),
});
