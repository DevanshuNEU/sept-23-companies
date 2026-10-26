import type { Id } from './types';

export const queryKeys = {
  shell: ['shell'],
  pulse: ['pulse'],
  search: ['search'],
  today: ['today'],
  outbox: ['outbox'],
  outboxDetail: (touchId: Id) => ['outbox', touchId],
  linkedin: ['linkedin'],
  needsYou: ['needs-you'],
  noGo: ['no-go'],
  company: (id: Id) => ['company', id],
  done: ['done'],
  plan: ['plan'],
  outcomePrompt: ['outcome-prompt'],
  wins: ['wins'],
  progress: ['progress'],
  findings: ['findings'],
  watch: ['watch'],
  score: (id: Id) => ['score', id],
} as const;

export const pollMs = { outbox: 15000, default: 60000, pulse: 5000 } as const;

export function newRequestId(): string {
  return crypto.randomUUID();
}
