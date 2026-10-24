import type { LiveMethods } from './client';
import { getJson, sendJson } from './api';
import type { Transport } from './transport';

/**
 * The No-go list (`GET /api/no-go`) and taking an entry off it (`POST /api/no-go/{id}/lift`). A lift answers `{}`;
 * the page reloads the list. Bringing back a removed company is a Today write (`restoreCompany`), so this view goes
 * live only with Today.
 */
export const noGoMethods = (t: Transport): LiveMethods => ({
  noGo: () => getJson(t, '/api/no-go'),
  liftNoGo: async (noGoId, reason, o) => {
    await sendJson(t, 'post', '/api/no-go/{no_go_id}/lift', { reason }, o, { params: { no_go_id: noGoId } });
  },
});
