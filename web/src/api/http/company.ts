import type { LiveMethods } from './client';
import { getJson } from './api';
import type { Transport } from './transport';

/** The company page: `GET /api/companies/{id}` (a merged company answers as its survivor). */
export const companyMethods = (t: Transport): LiveMethods => ({
  company: (companyId) => getJson(t, '/api/companies/{company_id}', { params: { company_id: companyId } }),
});

/** Search: the index, posting search and company search (the last is not on `OutreachClient` yet). */
export const searchMethods = (t: Transport): LiveMethods => ({
  searchIndex: () => getJson(t, '/api/search/index'),
  searchPostings: (q) => getJson(t, '/api/search/postings', { query: { q } }),
  searchCompanies: (q) => getJson(t, '/api/search/companies', { query: { q } }),
});
