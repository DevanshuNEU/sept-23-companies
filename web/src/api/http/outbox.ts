import type { LiveMethods } from './client';
import { getJson } from './api';
import type { Transport } from './transport';

/**
 * The Outbox reads: the list with the sending strip (`GET /api/outbox`), one thread (`GET /api/outbox/{touchId}`)
 * and two drafts at one company side by side (`GET /api/outbox/compare`). Its writes come with #11 Task 19.
 */
export const outboxMethods = (t: Transport): LiveMethods => ({
  outbox: () => getJson(t, '/api/outbox'),
  outboxDetail: (touchId) => getJson(t, '/api/outbox/{touch_id}', { params: { touch_id: touchId } }),
  compareDrafts: (messageId, otherMessageId) => getJson(t, '/api/outbox/compare', { query: { messageId, otherMessageId } }),
});
