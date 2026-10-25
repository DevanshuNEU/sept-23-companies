import type { LiveMethods } from './client';
import { getJson, sendJson } from './api';
import type { Transport } from './transport';

/**
 * The global pause and resume (`POST /api/sending/pause`, `/resume`). Both carry the sender's revision, read when
 * the write commits (as the company writes read theirs), so a pause or resume that crossed another one is refused.
 * A resume after a hard block needs `acknowledgeBlock`; the page sends it only from the dialog that shows the
 * block's reason, and the API refuses without it.
 */
export const sendingMethods = (t: Transport): LiveMethods => ({
  pauseSending: async (o) => {
    const { revision } = await getJson(t, '/api/sending');
    return sendJson(t, 'post', '/api/sending/pause', { revision }, o);
  },
  resumeSending: async (acknowledgeBlock, o) => {
    const { revision } = await getJson(t, '/api/sending');
    return sendJson(t, 'post', '/api/sending/resume', { revision, acknowledgeBlock }, o);
  },
});
