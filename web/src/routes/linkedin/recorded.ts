import type { LinkedInGroup, LinkedInPerson } from '@/api';

/** Someone he messaged who has not replied yet, with the company the list groups them under. */
export interface Recorded {
  group: LinkedInGroup;
  person: LinkedInPerson;
}

/** People with a request logged and no reply: the ones a reply can be marked for. */
export function recordedWithoutReply(groups: LinkedInGroup[]): Recorded[] {
  return groups.flatMap((group) =>
    group.people
      .filter((p) => p.steps.some((s) => s.step === 'request_sent') && !p.steps.some((s) => s.step === 'replied'))
      .map((person) => ({ group, person })),
  );
}
