import type { ResolvedReview, ReviewItem } from '@/api';
import { companyRows, resolvedGroups, byPriority } from './groups';

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

describe('byPriority', () => {
  const open = (id: number, company: string, o: { picked?: boolean; inPlay?: boolean; holdsEmail?: boolean; ageDays?: number; kind?: ReviewItem['kind'] } = {}): ReviewItem =>
    ({
      id, kind: o.kind ?? 'size_unknown', company: { id, name: company, picked: o.picked ?? false }, holdsEmail: o.holdsEmail ?? false, inPlay: o.inPlay ?? false,
      ageDays: o.ageDays ?? 0,
    }) as unknown as ReviewItem;
  const names = (items: ReviewItem[]) => byPriority(companyRows(items)).map((r) => r.lead.company.name);

  it('puts what he picked today first, then what holds up an email, then what is in play, then the rest', () => {
    const items = [
      open(1, 'Waiting', { ageDays: 13 }),
      open(2, 'In play', { inPlay: true }),
      open(3, 'Emailing', { holdsEmail: true }),
      open(4, 'Picked', { picked: true }),
    ];
    expect(names(items)).toEqual(['Picked', 'Emailing', 'In play', 'Waiting']);
  });

  it('within a tier the newer question comes first, and equal ones keep the API order', () => {
    const items = [open(1, 'Old', { ageDays: 9 }), open(2, 'New', { ageDays: 1 }), open(3, 'Same a', { ageDays: 4 }), open(4, 'Same b', { ageDays: 4 })];
    expect(names(items)).toEqual(['New', 'Same a', 'Same b', 'Old']);
  });

  it('a merged row ranks by its best item: picked if any is, youngest age', () => {
    const items = [
      open(1, 'Plain', { holdsEmail: true, ageDays: 2 }),
      { ...open(2, 'Both', { ageDays: 12 }), kind: 'size_unknown' as const },
      { ...open(2, 'Both', { picked: true, ageDays: 0 }), id: 3, kind: 'domain_missing' as const },
    ];
    expect(names(items)).toEqual(['Both', 'Plain']);
  });
});
