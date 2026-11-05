import type { Id, OutboxItem } from '@/api';
import { buildFixture } from '@/api/fixture/data';
import { approvedText, approveTitle, batchSummary, cleanDrafts, heldBack, heldBackText, refusedSummary } from './approve-batch';
import { approveRows } from './outbox-patches';

const base = (): OutboxItem => {
  const found = buildFixture().outbox.items.find((i) => i.group === 'review' && i.state === 'drafted' && i.blockCount === 0 && i.warnCount === 0);
  if (!found) throw new Error('the fixture has no clean draft');
  return found;
};
let next = 9000;
const draft = (over: Partial<OutboxItem> = {}): OutboxItem => {
  next += 1;
  return { ...base(), touchId: next, messageId: next + 3000, seq: 0, ...over };
};
const none = () => false;

describe('cleanDrafts', () => {
  it('keeps a clean first email and a clean follow-up, in order', () => {
    const a = draft();
    const b = draft({ seq: 2 });
    expect(cleanDrafts([a, b], none)).toEqual([a, b]);
  });

  it.each<[string, Partial<OutboxItem>]>([
    ['a block', { blockCount: 1 }],
    ['a warning', { warnCount: 1 }],
    ['a closed thread', { touchState: 'closed' }],
    ['a paused company', { group: 'paused' }],
    ['an approved email', { state: 'approved', group: 'approved' }],
  ])('leaves out a draft with %s', (_, over) => {
    expect(cleanDrafts([draft(over)], none)).toEqual([]);
  });

  it('leaves out a draft with unsaved text', () => {
    const typed = draft();
    const other = draft();
    expect(cleanDrafts([typed, other], (id: Id) => id === typed.messageId)).toEqual([other]);
  });
});

describe('heldBack', () => {
  it('counts each draft once: a block first, then a warning, then unsaved text', () => {
    const items = [
      draft({ blockCount: 1, warnCount: 1 }),
      draft({ warnCount: 2 }),
      draft(),
      draft(),
    ];
    const typed = items[3]!.messageId;
    expect(heldBack(items, (id) => id === typed)).toEqual({ warnings: 1, blocked: 1, unsaved: 1 });
  });

  it('ignores rows that are not open drafts in review', () => {
    expect(heldBack([draft({ group: 'sent', state: 'sent' }), draft({ group: 'paused', warnCount: 1 })], none)).toEqual({
      warnings: 0,
      blocked: 0,
      unsaved: 0,
    });
  });
});

describe('copy', () => {
  it('heldBackText says what stays and reads right in the singular', () => {
    expect(heldBackText({ warnings: 2, blocked: 1, unsaved: 1 })).toBe('2 with warnings, 1 blocked, 1 with unsaved edits stay here for you.');
    expect(heldBackText({ warnings: 1, blocked: 0, unsaved: 0 })).toBe('1 with a warning stays here for you.');
    expect(heldBackText({ warnings: 0, blocked: 0, unsaved: 0 })).toBeNull();
  });

  it('batchSummary counts first emails and follow-ups and drops a zero part', () => {
    const mixed = [...Array.from({ length: 4 }, () => draft()), ...Array.from({ length: 7 }, () => draft({ seq: 2 }))];
    expect(batchSummary(mixed)).toBe('4 first emails and 7 follow-ups. No blocks, no warnings.');
    expect(batchSummary([draft({ seq: 1 })])).toBe('1 follow-up. No blocks, no warnings.');
    expect(batchSummary([draft()])).toBe('1 first email. No blocks, no warnings.');
  });

  it('approveTitle follows the count', () => {
    expect(approveTitle(11)).toBe('Approve 11 drafts');
    expect(approveTitle(1)).toBe('Approve 1 draft');
    expect(approveTitle(0)).toBe('Approve drafts');
  });

  it('approvedText is singular for one', () => {
    expect(approvedText(1)).toBe('Approved 1 draft.');
    expect(approvedText(11)).toBe('Approved 11 drafts.');
  });

  it('refusedSummary names one refusal, and strips the server closing sentence', () => {
    expect(refusedSummary(10, 11, [{ name: 'Blake Graham', message: 'The draft changed since you loaded it. Nothing was saved.' }])).toBe(
      'Approved 10 of 11. Blake Graham was refused: The draft changed since you loaded it. It is back in Drafts to review.',
    );
    expect(refusedSummary(10, 11, [{ name: 'Blake Graham', message: 'This draft cannot be approved: x. Fix it, then approve again. Nothing was approved.' }])).toContain(
      'refused: This draft cannot be approved: x. Fix it, then approve again. It is back',
    );
  });

  it('refusedSummary lists several refusals', () => {
    expect(
      refusedSummary(9, 11, [
        { name: 'Blake Graham', message: 'It changed.' },
        { name: 'Ava Moreno', message: 'Only a draft can be approved.' },
      ]),
    ).toBe('Approved 9 of 11. Refused: Blake Graham (It changed); Ava Moreno (Only a draft can be approved). They are back in Drafts to review.');
  });

  it('refusedSummary starts with Nothing was approved when none went through', () => {
    expect(refusedSummary(0, 2, [{ name: 'A B', message: 'No.' }, { name: 'C D', message: 'No.' }])).toBe(
      'Nothing was approved. Refused: A B (No); C D (No). They are back in Drafts to review.',
    );
  });
});

describe('approveRows', () => {
  it('moves the named rows to Approved and recounts', () => {
    const view = buildFixture().outbox;
    const clean = cleanDrafts(view.items, none).slice(0, 2);
    const out = approveRows(view, new Set(clean.map((i) => i.touchId)));
    expect(out.counts.review).toBe(view.counts.review - 2);
    expect(out.counts.approved).toBe(view.counts.approved + 2);
    for (const c of clean) {
      const row = out.items.find((i) => i.touchId === c.touchId);
      expect(row).toMatchObject({ state: 'approved', group: 'approved' });
    }
    const untouched = view.items.find((i) => !clean.some((c) => c.touchId === i.touchId));
    expect(out.items.find((i) => i.touchId === untouched?.touchId)).toEqual(untouched);
  });
});
