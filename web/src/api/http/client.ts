import type { OutreachClient } from '../client';
import type { CompanyHit } from '../types';
import { companyMethods, searchMethods } from './company';
import { healthMethods } from './health';
import { createTransport, type Transport } from './transport';

/** The methods the API serves so far. Each view's task adds one factory below. */
export type LiveMethods = Partial<Omit<OutreachClient, 'source'>> & { searchCompanies?: (q: string) => Promise<CompanyHit[]> };
export type HttpClient = LiveMethods & { readonly source: 'live' };

/** One factory per view: it makes that view's methods from the transport. Each view's task adds one. */
const factories: ((t: Transport) => LiveMethods)[] = [healthMethods, companyMethods, searchMethods];

export function createHttpClient(t: Transport = createTransport()): HttpClient {
  return Object.assign({ source: 'live' as const }, ...factories.map((f) => f(t))) as HttpClient;
}
