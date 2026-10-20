import { useState } from 'react';

interface Anchor<K> {
  id: K;
  /** The group the selected item was in when last seen in place. */
  group: string;
  /** Where it sat in the list then. */
  index: number;
}

const same = <K,>(a: Anchor<K> | null, b: Anchor<K> | null) =>
  a === b || (a !== null && b !== null && a.id === b.id && a.group === b.group && a.index === b.index);

/**
 * Keeps what he is looking at where it was. A poll can move the selected item to another group
 * (approved, sent): while it is selected the item stays at its old position and everything else
 * follows the new order. Selecting something else lets it go.
 */
export function useStableOrder<T, K extends string | number>(
  items: T[],
  selected: K | null,
  idOf: (t: T) => K,
  groupOf: (t: T) => string,
): T[] {
  const [anchor, setAnchor] = useState<Anchor<K> | null>(null);

  let result = items;
  let next: Anchor<K> | null = null;
  const at = selected === null ? -1 : items.findIndex((t) => idOf(t) === selected);
  const current = at === -1 ? undefined : items[at];
  if (selected !== null && current !== undefined) {
    const group = groupOf(current);
    if (anchor !== null && anchor.id === selected && anchor.group !== group) {
      // Moved to another group by a refresh: hold the old slot.
      next = anchor;
      const rest = items.filter((_, i) => i !== at);
      rest.splice(Math.min(anchor.index, rest.length), 0, current);
      result = rest;
    } else {
      next = { id: selected, group, index: at };
    }
  }

  // Adjusting state while rendering (React re-runs this render at once; the second pass is a no-op).
  if (!same(anchor, next)) setAnchor(next);
  return result;
}
