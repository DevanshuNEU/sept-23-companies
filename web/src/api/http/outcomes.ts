import type { LiveMethods } from './client';
import { getJson, sendJson } from './api';
import type { Transport } from './transport';

/** The prompt (`GET /api/outcomes/prompt`) and an answer to it. "Nothing yet" is `kind: 'quiet'`: a snooze before day 21. */
export const outcomeMethods = (t: Transport): LiveMethods => ({
  outcomePrompt: () => getJson(t, '/api/outcomes/prompt'),
  logOutcome: (touchId, kind, revision, o, replyKind) =>
    sendJson(t, 'post', '/api/touches/{touch_id}/outcomes', replyKind ? { kind, replyKind, revision } : { kind, revision }, o, {
      params: { touch_id: touchId },
    }),
});
