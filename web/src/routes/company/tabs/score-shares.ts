/**
 * The signed shares (a fraction each, signed, summing to 1 in absolute value) as whole percents whose absolute values
 * add to exactly 100, by the largest remainder. Plain rounding can print 101. Every neutral signal gives 0.
 */
export function wholePercents(shares: number[]): number[] {
  const exact = shares.map((s) => Math.abs(s) * 100);
  const target = Math.round(exact.reduce((n, x) => n + x, 0));
  const floors = exact.map(Math.floor);
  let left = target - floors.reduce((n, x) => n + x, 0);
  const byRemainder = exact
    .map((x, i) => ({ i, rest: x - Math.floor(x) }))
    .filter((r) => r.rest > 0)
    .sort((a, b) => b.rest - a.rest || a.i - b.i);
  for (const { i } of byRemainder) {
    if (left <= 0) break;
    floors[i] = (floors[i] ?? 0) + 1;
    left -= 1;
  }
  return shares.map((s, i) => (s < 0 ? -(floors[i] ?? 0) : (floors[i] ?? 0)));
}
