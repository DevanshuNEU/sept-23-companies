import type { NeedsYouView, ResolvedReview, ReviewItem } from '@/api';
import { buildFixture } from '@/api/fixture/data';
import { settleView } from './patches';

const view = (): NeedsYouView => buildFixture().needsYou;
const hexOf = (d: NeedsYouView): ReviewItem => {
  const hex = d.open.find((r) => r.company.name === 'Hex');
  if (!hex) throw new Error('no Hex');
  return hex;
};
const NO_GO = { resolution: 'marked_no_go', note: 'Added to the no-go list by you: does not sponsor', locked: false } as const;

/** What the server answers once the no-go is stored: Hex has left `open` and its real row leads Resolved. */
function served(before: NeedsYouView, hex: ReviewItem): NeedsYouView {
  const row: ResolvedReview = {
    id: hex.id, kind: hex.kind, companyName: 'Hex', resolution: 'marked_no_go', note: 'Added to the no-go list by you: does not sponsor',
    at: '2026-11-10T15:15:00Z', locked: false, revision: 7, canUndo: true,
  };
  return { ...before, open: before.open.filter((x) => x.id !== hex.id), settledToday: before.settledToday + 1, resolvedThisWeek: [row, ...before.resolvedThisWeek] };
}

describe('settleView', () => {
  it('while the item is open, takes it out, counts it once and shows a placeholder row with no Undo', () => {
    const d = view();
    const hex = hexOf(d);
    const after = settleView(d, hex, NO_GO);
    expect(after.open.some((x) => x.id === hex.id)).toBe(false);
    expect(after.settledToday).toBe(d.settledToday + 1);
    expect(after.resolvedThisWeek[0]).toMatchObject({ id: hex.id, canUndo: false, revision: hex.revision + 1 });
  });

  it('on the refetched payload that already holds the settling, changes nothing: the real row keeps its Undo and revision', () => {
    const d = view();
    const hex = hexOf(d);
    const fresh = served(d, hex);
    const after = settleView(fresh, hex, NO_GO);
    expect(after).toBe(fresh);
    expect(after.resolvedThisWeek[0]).toMatchObject({ id: hex.id, canUndo: true, revision: 7 });
    expect(after.settledToday).toBe(d.settledToday + 1);
  });

  it('applied again to its own result (a refetch that failed and kept the old data patched), never counts twice', () => {
    const d = view();
    const hex = hexOf(d);
    const once = settleView(d, hex, NO_GO);
    const twice = settleView(once, hex, NO_GO);
    expect(twice.settledToday).toBe(d.settledToday + 1);
    expect(twice.resolvedThisWeek.filter((x) => x.id === hex.id)).toHaveLength(1);
  });
});
