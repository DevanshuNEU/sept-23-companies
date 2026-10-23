import type { OutreachClient } from '../client';
import { healthMethods } from './health';
import { createTransport, type Transport } from './transport';

/** The methods the API serves so far. Each view's task adds one factory below. */
export type LiveMethods = Partial<Omit<OutreachClient, 'source'>>;
export type HttpClient = LiveMethods & { readonly source: 'live' };

/** One factory per view: it makes that view's methods from the transport. Each view's task adds one. */
const factories: ((t: Transport) => LiveMethods)[] = [healthMethods];

export function createHttpClient(t: Transport = createTransport()): HttpClient {
  return Object.assign({ source: 'live' as const }, ...factories.map((f) => f(t))) as HttpClient;
}
