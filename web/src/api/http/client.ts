import type { OutreachClient, WriteOptions } from '../client';
import type { CompanyHit, FindingsView, Id, LinkedInPerson, LinkedInStep, OutcomeKind, OutcomePromptView, ProgressView, ReplyKind, Revision, WatchView, WinsView } from '../types';
import { companyMethods, searchMethods } from './company';
import { healthMethods } from './health';
import { linkedinMethods } from './linkedin';
import { outcomeMethods } from './outcomes';
import { progressMethods } from './progress';
import { createTransport, type Transport } from './transport';
import { watchMethods } from './watch';

/** The methods the API serves so far. Each view's task adds one factory below. */
export type LiveMethods = Partial<Omit<OutreachClient, 'source'>> & {
  searchCompanies?: (q: string) => Promise<CompanyHit[]>;
  // Not on `OutreachClient` until #33 adds them.
  watchList?: () => Promise<WatchView>;
  dismissWatch?: (itemId: Id, o: WriteOptions) => Promise<void>;
  outcomePrompt?: () => Promise<OutcomePromptView>;
  progress?: () => Promise<ProgressView>;
  findings?: () => Promise<FindingsView>;
  wins?: () => Promise<WinsView>;
  markWinsSeen?: (winIds: Id[], o: WriteOptions) => Promise<void>;
  // Not on `OutreachClient`: the page undoes before a write commits, so only the API knows a committed undo.
  undoLinkedIn?: (personId: Id, step: LinkedInStep, o: WriteOptions) => Promise<LinkedInPerson>;
  logOutcome?: (touchId: Id, kind: OutcomeKind, revision: Revision, o: WriteOptions, replyKind?: Exclude<ReplyKind, 'unsorted' | 'auto_reply'>) => Promise<{ revision: Revision }>;
};
export type HttpClient = LiveMethods & { readonly source: 'live' };

/** One factory per view: it makes that view's methods from the transport. Each view's task adds one. */
const factories: ((t: Transport) => LiveMethods)[] = [healthMethods, companyMethods, searchMethods, watchMethods, outcomeMethods, progressMethods, linkedinMethods];

export function createHttpClient(t: Transport = createTransport()): HttpClient {
  return Object.assign({ source: 'live' as const }, ...factories.map((f) => f(t))) as HttpClient;
}
