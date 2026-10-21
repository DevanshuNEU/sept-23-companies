/** Groups items by key in order of first appearance, keeping each group's items in their given order. */
export function groupInOrder<T, K>(items: readonly T[], keyOf: (item: T) => K): { group: K; items: T[] }[] {
  const groups: { group: K; items: T[] }[] = [];
  for (const item of items) {
    const group = keyOf(item);
    const found = groups.find((g) => g.group === group);
    if (found) found.items.push(item);
    else groups.push({ group, items: [item] });
  }
  return groups;
}
