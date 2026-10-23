import type { LiveMethods } from './client';
import { sendJson } from './api';
import type { Transport } from './transport';

/**
 * The company writes: pause, resume, close and reopen (`POST /api/companies/{id}/...`, each with the company's
 * revision), and a person's role kind (`POST /api/people/{id}/role-kind`). Pause, close and reopen answer with the
 * new revision, which the page reads back on its refetch; resume says how many emails were waiting.
 */
export const companyWriteMethods = (t: Transport): LiveMethods => ({
  pauseCompany: async (companyId, revision, o) => {
    await sendJson(t, 'post', '/api/companies/{company_id}/pause', { revision }, o, { params: { company_id: companyId } });
  },
  resumeCompany: async (companyId, revision, o) => {
    const { released } = await sendJson(t, 'post', '/api/companies/{company_id}/resume', { revision }, o, { params: { company_id: companyId } });
    return { released };
  },
  closeCompany: async (companyId, reason, note, revision, o) => {
    await sendJson(t, 'post', '/api/companies/{company_id}/close', { reason, note, revision }, o, { params: { company_id: companyId } });
  },
  reopenCompany: async (companyId, revision, o) => {
    await sendJson(t, 'post', '/api/companies/{company_id}/reopen', { revision }, o, { params: { company_id: companyId } });
  },
  setRoleKind: (personId, kind, revision, o) =>
    sendJson(t, 'post', '/api/people/{person_id}/role-kind', { kind, revision }, o, { params: { person_id: personId } }),
});
