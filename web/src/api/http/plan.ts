import type { LiveMethods } from './client';
import { getJson } from './api';
import type { Transport } from './transport';

/**
 * The people plan (`GET /api/plan`): today's rows with the state, reason, note and search link the API derives, the
 * LinkedIn month, tomorrow's line and the week's counts. The page computes none of them.
 */
export const planMethods = (t: Transport): LiveMethods => ({
  plan: () => getJson(t, '/api/plan'),
});
