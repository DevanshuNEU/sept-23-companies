import type { IsoTime, ReadResult } from '@/api';

// Amber at most, never red (Devanshu, Q3 of the Needs you mockup): an error is a filled amber square and an empty
// read a hollow one, so the two differ by shape as well as by the label.
const SQUARE: Record<ReadResult, string> = {
  ok: 'bg-ok-dot',
  empty: 'border border-warn-dot',
  error: 'bg-warn-dot',
};

/** One square per board read, oldest first. The label lists the results so color is never the only signal. */
export function RunStrip({ reads }: { reads: { at: IsoTime; result: ReadResult }[] }) {
  return (
    <span
      role="img"
      aria-label={reads.map((r) => r.result).join(', ')}
      className="inline-flex gap-[3px] align-middle"
    >
      {reads.map((r, i) => (
        <i key={`${r.at}-${i}`} data-result={r.result} className={`size-2.5 rounded-[2px] ${SQUARE[r.result]}`} />
      ))}
    </span>
  );
}
