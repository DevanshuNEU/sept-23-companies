import type { LiveMethods } from './client';
import { getJson } from './api';
import type { Transport } from './transport';

/** The Done companies and the reply metrics (`GET /api/done`). Read only. */
export const doneMethods = (t: Transport): LiveMethods => ({
  done: () => getJson(t, '/api/done'),
});
