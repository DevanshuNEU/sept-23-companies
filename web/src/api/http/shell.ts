import type { LiveMethods } from './client';
import { getJson } from './api';
import type { Transport } from './transport';

/** The sidebar's counts, the sender line, today's plan and the wins not yet seen (`GET /api/shell`), and the pulse
 * (`GET /api/pulse`). Read only. */
export const shellMethods = (t: Transport): LiveMethods => ({
  shell: () => getJson(t, '/api/shell'),
  pulse: () => getJson(t, '/api/pulse'),
});
