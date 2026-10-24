import type { LiveMethods } from './client';
import { getJson, sendJson } from './api';
import type { Transport } from './transport';

/**
 * Progress (`GET /api/progress`), the findings (`GET /api/findings`), the win log (`GET /api/wins`) and marking wins
 * seen. A findings `month` is the report's label only: its counts read every touch so far. The seen answer is a
 * count the page does not need, so it resolves to nothing.
 */
export const progressMethods = (t: Transport): LiveMethods => ({
  progress: () => getJson(t, '/api/progress'),
  findings: () => getJson(t, '/api/findings'),
  wins: () => getJson(t, '/api/wins'),
  markWinsSeen: async (winIds, o) => {
    await sendJson(t, 'post', '/api/wins/seen', { winIds }, o);
  },
});
