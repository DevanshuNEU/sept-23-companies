import type { IsoTime, ReadResult } from '@/api';

const SQUARE: Record<ReadResult, string> = {
  ok: 'bg-ok-dot',
  empty: 'bg-warn-dot',
  error: 'bg-bad-dot',
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
