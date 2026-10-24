import type { LiveMethods } from './client';
import { getJson, sendJson } from './api';
import type { Transport } from './transport';

/**
 * The lean Today (`GET /api/today`) and the writes on its rows. Each write carries the company's revision, which is
 * the row's `revision`. Pick, unpick and backlog answer with the row as it now is; remove and restore answer `{}`.
 * #15's daily plan replaces what is behind these routes and keeps their shapes.
 */
export const todayMethods = (t: Transport): LiveMethods => ({
  today: () => getJson(t, '/api/today'),
  pick: (companyId, revision, o) => sendJson(t, 'post', '/api/companies/{company_id}/pick', { revision }, o, { params: { company_id: companyId } }),
  unpick: (companyId, revision, o) => sendJson(t, 'post', '/api/companies/{company_id}/unpick', { revision }, o, { params: { company_id: companyId } }),
  backlog: (companyId, revision, o) => sendJson(t, 'post', '/api/companies/{company_id}/backlog', { revision }, o, { params: { company_id: companyId } }),
  removeCompany: async (companyId, reason, note, revision, o) => {
    await sendJson(t, 'post', '/api/companies/{company_id}/remove', { reason, note, revision }, o, { params: { company_id: companyId } });
  },
  restoreCompany: async (companyId, reason, revision, o) => {
    await sendJson(t, 'post', '/api/companies/{company_id}/restore', { reason, revision }, o, { params: { company_id: companyId } });
  },
});
