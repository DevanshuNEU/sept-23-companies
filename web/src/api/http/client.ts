import type { OutreachClient, WriteOptions } from '../client';
import type { CompanyHit, Id, LinkedInPerson, LinkedInStep, Revision } from '../types';
import { companyMethods, searchMethods } from './company';
import { companyWriteMethods } from './company-writes';
import { doneMethods } from './done';
import { healthMethods } from './health';
import { linkedinMethods } from './linkedin';
import { needsYouMethods } from './needs-you';
import { noGoMethods } from './no-go';
import { outboxMethods } from './outbox';
import { outcomeMethods } from './outcomes';
import { planMethods } from './plan';
import { progressMethods } from './progress';
import { sendingMethods } from './sending';
import { createTransport, type Transport } from './transport';
import { shellMethods } from './shell';
import { todayMethods } from './today';
import { watchMethods } from './watch';

/** The methods the API serves so far. Each view's task adds one factory below. */
export type LiveMethods = Partial<Omit<OutreachClient, 'source'>> & {
  searchCompanies?: (q: string) => Promise<CompanyHit[]>;
  // Not on `OutreachClient`: the page undoes before a write commits, so only the API knows a committed undo.
  undoLinkedIn?: (personId: Id, step: LinkedInStep, o: WriteOptions) => Promise<LinkedInPerson>;
  // Not on `OutreachClient` until #16 adds the merge confirm and its undo: take a merged company back out.
  unmergeCompany?: (companyId: Id, revision: Revision, o: WriteOptions) => Promise<{ revision: Revision }>;
};
export type HttpClient = LiveMethods & { readonly source: 'live' };

/** One factory per view: it makes that view's methods from the transport. Each view's task adds one. */
const factories: ((t: Transport) => LiveMethods)[] = [healthMethods, companyMethods, companyWriteMethods, searchMethods, watchMethods, outcomeMethods, planMethods, progressMethods, linkedinMethods, needsYouMethods, outboxMethods, sendingMethods, noGoMethods, doneMethods, todayMethods, shellMethods];

export function createHttpClient(t: Transport = createTransport()): HttpClient {
  return Object.assign({ source: 'live' as const }, ...factories.map((f) => f(t))) as HttpClient;
}
