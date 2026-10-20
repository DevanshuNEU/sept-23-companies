import { renderHook } from '@testing-library/react';
import { useStableOrder } from '@/lib/stable-order';

interface Row {
  id: string;
  group: string;
}
const row = (id: string, group: string): Row => ({ id, group });
const ids = (rows: Row[]) => rows.map((r) => r.id);

function setup(initial: Row[], selected: string | null) {
  return renderHook(
    (p: { items: Row[]; selected: string | null }) =>
      useStableOrder(p.items, p.selected, (r) => r.id, (r) => r.group),
    { initialProps: { items: initial, selected } },
  );
}

describe('useStableOrder', () => {
  it('keeps the selected item in place across a refresh', () => {
    const { result, rerender } = setup([row('A', 'review'), row('B', 'review'), row('C', 'review')], 'B');
    expect(ids(result.current)).toEqual(['A', 'B', 'C']);

    // The poll says B is approved now, so the server sorts it to the next group.
    const refreshed = [row('A', 'review'), row('C', 'review'), row('B', 'approved')];
    rerender({ items: refreshed, selected: 'B' });
    expect(ids(result.current)).toEqual(['A', 'B', 'C']);

    // Another poll with the same answer changes nothing.
    rerender({ items: [...refreshed], selected: 'B' });
    expect(ids(result.current)).toEqual(['A', 'B', 'C']);

    // Moving the selection lets B go to where the server put it.
    rerender({ items: refreshed, selected: 'C' });
    expect(ids(result.current)).toEqual(['A', 'C', 'B']);
  });

  it('follows the new order when nothing is selected', () => {
    const { result, rerender } = setup([row('A', 'review'), row('B', 'review')], null);
    rerender({ items: [row('B', 'approved'), row('A', 'review')], selected: null });
    expect(ids(result.current)).toEqual(['B', 'A']);
  });

  it('follows the new order while the selected item stays in its group', () => {
    const { result, rerender } = setup([row('A', 'review'), row('B', 'review'), row('C', 'review')], 'B');
    rerender({ items: [row('C', 'review'), row('B', 'review'), row('A', 'review')], selected: 'B' });
    expect(ids(result.current)).toEqual(['C', 'B', 'A']);
  });

  it('lets the item go when it returns to its first group', () => {
    const { result, rerender } = setup([row('A', 'review'), row('B', 'review'), row('C', 'review')], 'B');
    rerender({ items: [row('A', 'review'), row('C', 'review'), row('B', 'approved')], selected: 'B' });
    rerender({ items: [row('B', 'review'), row('A', 'review'), row('C', 'review')], selected: 'B' });
    expect(ids(result.current)).toEqual(['B', 'A', 'C']);
  });

  it('keeps a pinned item inside the list when the list shrinks', () => {
    const { result, rerender } = setup([row('A', 'review'), row('B', 'review'), row('C', 'review')], 'C');
    rerender({ items: [row('A', 'review'), row('C', 'approved')], selected: 'C' });
    expect(ids(result.current)).toEqual(['A', 'C']);
  });

  it('drops the pin when the selected item leaves the list', () => {
    const { result, rerender } = setup([row('A', 'review'), row('B', 'review'), row('C', 'review')], 'B');
    rerender({ items: [row('A', 'review'), row('C', 'review')], selected: 'B' });
    expect(ids(result.current)).toEqual(['A', 'C']);
  });

  it('works with numeric ids', () => {
    const { result } = renderHook(() =>
      useStableOrder([{ n: 1, g: 'x' }, { n: 2, g: 'x' }], 2, (r) => r.n, (r) => r.g),
    );
    expect(result.current.map((r) => r.n)).toEqual([1, 2]);
  });
});
