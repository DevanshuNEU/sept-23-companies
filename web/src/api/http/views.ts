import type { OutreachClient } from '../client';

export type ViewName =
  | 'health'
  | 'shell'
  | 'search'
  | 'company'
  | 'plan'
  | 'outcomes'
  | 'progress'
  | 'wins'
  | 'watch'
  | 'today'
  | 'linkedin'
  | 'outbox'
  | 'sending'
  | 'needs-you'
  | 'no-go'
  | 'done';

export const ALL_VIEWS: readonly ViewName[] = [
  'health', 'shell', 'search', 'company', 'plan', 'outcomes', 'progress', 'wins', 'watch', 'today', 'linkedin', 'outbox', 'sending', 'needs-you', 'no-go', 'done',
];

/** Every method of the client and the view that serves it. A method added to the interface without a line here fails `pnpm typecheck`. */
export const VIEW_OF_METHOD: Record<Exclude<keyof OutreachClient, 'source'>, ViewName> = {
  health: 'health',
  shell: 'shell',
  searchIndex: 'search',
  searchPostings: 'search',
  today: 'today',
  pick: 'today',
  unpick: 'today',
  backlog: 'today',
  removeCompany: 'today',
  restoreCompany: 'today',
  outbox: 'outbox',
  outboxDetail: 'outbox',
  saveMessage: 'outbox',
  approve: 'outbox',
  unapprove: 'outbox',
  skip: 'outbox',
  setReplyKind: 'outbox',
  compareDrafts: 'outbox',
  // The company writes belong to the company page's API (the Outbox uses them too).
  setRoleKind: 'company',
  pauseCompany: 'company',
  resumeCompany: 'company',
  releaseHold: 'company',
  closeCompany: 'company',
  reopenCompany: 'company',
  pauseSending: 'sending',
  resumeSending: 'sending',
  linkedin: 'linkedin',
  logLinkedIn: 'linkedin',
  needsYou: 'needs-you',
  resolveReview: 'needs-you',
  noGo: 'no-go',
  liftNoGo: 'no-go',
  company: 'company',
  done: 'done',
};

/** The views served by the API unless `?live=` or `VITE_LIVE_VIEWS` says otherwise. Each view's task adds its name. */
export const DEFAULT_LIVE_VIEWS: readonly ViewName[] = ['health', 'search', 'company', 'watch', 'outcomes', 'progress', 'wins', 'linkedin', 'needs-you', 'done', 'today', 'no-go'];
