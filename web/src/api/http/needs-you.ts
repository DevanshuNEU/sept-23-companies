import type { LiveMethods } from './client';
import { getJson, sendJson } from './api';
import type { Transport } from './transport';

/**
 * Needs you (`GET /api/needs-you`), resolving an item with one fix (ok, edit, no-go, or merge for a possible
 * duplicate), taking a settling back (Undo from Resolved, which also unmerges a merge) and taking a merged company
 * back out. A resolve or a reopen answers with the item's new revision, which the page does not need: it reloads the list.
 */
export const needsYouMethods = (t: Transport): LiveMethods => ({
  needsYou: () => getJson(t, '/api/needs-you'),
  resolveReview: async (reviewId, fix, revision, o) => {
    await sendJson(t, 'post', '/api/review/{review_id}/resolve', { fix, revision }, o, { params: { review_id: reviewId } });
  },
  reopenReview: async (reviewId, revision, o) => {
    await sendJson(t, 'post', '/api/review/{review_id}/reopen', { revision }, o, { params: { review_id: reviewId } });
  },
  unmergeCompany: (companyId, revision, o) =>
    sendJson(t, 'post', '/api/companies/{company_id}/unmerge', { revision }, o, { params: { company_id: companyId } }),
});
