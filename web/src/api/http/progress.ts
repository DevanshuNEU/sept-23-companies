import type { LiveMethods } from './client';
import { getJson, sendJson } from './api';
import type { Transport } from './transport';

/**
 * Progress (`GET /api/progress`), the monthly findings (`GET /api/findings`), the win log (`GET /api/wins`) and
 * marking wins seen. The seen answer is a count the page does not need, so it resolves to nothing.
 */
export const progressMethods = (t: Transport): LiveMethods => ({
  progress: () => getJson(t, '/api/progress'),
  findings: () => getJson(t, '/api/findings'),
  wins: () => getJson(t, '/api/wins'),
  markWinsSeen: async (winIds, o) => {
    await sendJson(t, 'post', '/api/wins/seen', { winIds }, o);
  },
});
