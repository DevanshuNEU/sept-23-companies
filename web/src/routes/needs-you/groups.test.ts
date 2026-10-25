import type { ResolvedReview } from '@/api';
import { resolvedGroups } from './groups';

const row = (id: number, at: string): ResolvedReview => ({
  id, kind: 'size_unknown', companyName: `Co ${id}`, resolution: 'marked_ok', note: 'Marked ok by you', at, locked: false, revision: 2, canUndo: true,
});

describe('resolvedGroups', () => {
  // Tue Nov 10, 10:14 in New York; the New York week began Mon Nov 9.
  const asOf = '2026-11-10T15:14:00Z';

  it('labels by the real day: Today, Earlier this week, then each older day by its date', () => {
    const rows = [
      row(1, '2026-11-10T14:12:00Z'), // Tue Nov 10, 09:12
      row(2, '2026-11-09T16:30:00Z'), // Mon Nov 9
      row(3, '2026-11-07T04:30:00Z'), // Fri Nov 6, 23:30 in New York
      row(4, '2026-11-06T15:00:00Z'), // Fri Nov 6
      row(5, '2026-11-05T15:00:00Z'), // Thu Nov 5
    ];
    expect(resolvedGroups(rows, asOf).map((g) => [g.title, g.rows.map((r) => r.id)])).toEqual([
      ['Today', [1]],
      ['Earlier this week', [2]],
      ['Fri Nov 6', [3, 4]],
      ['Thu Nov 5', [5]],
    ]);
  });

  it('on a Monday, yesterday is last week and reads as its date', () => {
    const monday = '2026-11-09T15:00:00Z';
    expect(resolvedGroups([row(1, '2026-11-08T15:00:00Z')], monday).map((g) => g.title)).toEqual(['Sun Nov 8']);
  });

  it('a Sunday row stays in the week that began the Monday before', () => {
    const sunday = '2026-11-15T15:00:00Z';
    expect(resolvedGroups([row(1, '2026-11-09T15:00:00Z')], sunday).map((g) => g.title)).toEqual(['Earlier this week']);
  });
});
