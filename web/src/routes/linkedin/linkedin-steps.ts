import type { LinkedInPerson, LinkedInStep } from '@/api';

export const ORDER: LinkedInStep[] = ['request_sent', 'accepted', 'message_sent', 'replied'];

/** The button for each step: its words and key. */
export const NEXT: Record<LinkedInStep, { label: string; key: string }> = {
  request_sent: { label: 'Log request', key: 'R' },
  accepted: { label: 'Log accepted', key: 'A' },
  message_sent: { label: 'Log message', key: 'M' },
  replied: { label: 'Log reply', key: 'Y' },
};

/** The step after the furthest one logged, or null once they replied. */
export function nextStep(p: LinkedInPerson): LinkedInStep | null {
  const reached = Math.max(-1, ...p.steps.map((s) => ORDER.indexOf(s.step)));
  return ORDER[reached + 1] ?? null;
}

/** A step in the words of the "Already logged" toast. */
export const STEP_WORD: Record<LinkedInStep, string> = {
  request_sent: 'request sent',
  accepted: 'accepted',
  message_sent: 'message sent',
  replied: 'reply',
};
