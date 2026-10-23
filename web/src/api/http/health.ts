import type { LiveMethods } from './client';
import { getJson } from './api';
import type { Transport } from './transport';

/** The health view: `GET /api/health`. */
export const healthMethods = (t: Transport): LiveMethods => ({
  health: () => getJson(t, '/api/health'),
});
