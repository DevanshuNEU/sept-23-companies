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
  reopenReview: 'needs-you',
  noGo: 'no-go',
  liftNoGo: 'no-go',
  company: 'company',
  done: 'done',
  // The people plan and its writes (#33, #11 Tasks 10 and 11); Not a fit is the Today view's removeCompany.
  plan: 'plan',
  logRequest: 'plan',
  logApplied: 'plan',
  skipRow: 'plan',
  saveNote: 'plan',
  confirmReason: 'plan',
  outcomePrompt: 'outcomes',
  logOutcome: 'outcomes',
  progress: 'progress',
  findings: 'progress',
  wins: 'wins',
  markWinsSeen: 'wins',
  watchList: 'watch',
  dismissWatch: 'watch',
  score: 'company',
};

/**
 * The views that have a method on the client. Only these can be sample data, so only these decide `source` (the
 * "Sample data" badge) and whether the fixture chunk loads: a view with no method on `OutreachClient` has nothing to be.
 */
export const SERVED_VIEWS: readonly ViewName[] = ALL_VIEWS.filter((v) => Object.values(VIEW_OF_METHOD).includes(v));

/** The views served by the API unless `?live=` or `VITE_LIVE_VIEWS` says otherwise. Each view's task adds its name. */
export const DEFAULT_LIVE_VIEWS: readonly ViewName[] = ['health', 'search', 'company', 'plan', 'watch', 'outcomes', 'progress', 'wins', 'linkedin', 'needs-you', 'done', 'today', 'no-go', 'outbox', 'sending', 'shell'];
