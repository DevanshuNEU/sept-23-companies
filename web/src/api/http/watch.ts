import type { LiveMethods } from './client';
import { getJson, sendJson } from './api';
import type { Transport } from './transport';

/** The watch list: `GET /api/watch`, and a dismiss (the answer is the id sent; the page needs nothing from it). */
export const watchMethods = (t: Transport): LiveMethods => ({
  watchList: () => getJson(t, '/api/watch'),
  dismissWatch: async (itemId, o) => {
    await sendJson(t, 'post', '/api/watch/{item_id}/dismiss', {}, o, { params: { item_id: itemId } });
  },
});
