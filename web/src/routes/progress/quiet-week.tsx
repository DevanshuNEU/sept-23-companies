import type { BaseRates, ProgressPeriod } from '@/api';
import { baseRateParagraph } from '@/lib/progress';
import { formatDateMonthDay } from '@/routes/today/short-dates';
import { EffortList, FunnelLedger } from './funnel-ledger';

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** The muted line under the paragraph: where the rate comes from, in his own counts. Null when there is nothing to cite. */
function sourceLine(base: BaseRates): string | null {
  const since = formatDateMonthDay(base.since);
  if (!base.enough) return base.requests > 0 ? `${plural(base.requests, 'request', 'requests')} so far, since ${since}.` : null;
  return `From your own ${base.requests} requests since ${since}: ${base.accepts} accepted, ${plural(base.replies, 'reply', 'replies')}.`;
}

/**
 * A period with requests and no replies keeps the page and changes the order: the work first, his own usual rate
 * second, the counts last and muted. The paragraph says only what his numbers say. With too few requests it says what
 * it takes; with enough requests and no reply ever it is left out, so there is never a "1 in 0". Zero is plain grey.
 */
export function QuietWeek({ period, base, noun = 'week' }: { period: ProgressPeriod; base: BaseRates; noun?: 'week' | 'month' }) {
  const paragraph = baseRateParagraph(base);
  const source = paragraph === null ? null : sourceLine(base);
  return (
    <div className="grid gap-6">
      <section aria-labelledby="quiet-work-h" className="grid gap-1.5">
        <h3 id="quiet-work-h" className="text-base font-semibold">{`Your work this ${noun}`}</h3>
        <EffortList period={period} label={`Your work this ${noun}`} large />
      </section>

      {paragraph ? (
        <div className="grid gap-1.5 rounded-lg border bg-muted/50 px-5 py-4">
          <p className="text-base">{paragraph}</p>
          {source ? <p className="text-[13px] text-muted-foreground">{source}</p> : null}
        </div>
      ) : null}

      <section aria-labelledby="quiet-counts-h" className="grid gap-2">
        <h3 id="quiet-counts-h" className="text-sm font-semibold">{`The ${noun} in counts`}</h3>
        <FunnelLedger period={period} minRequests={base.minRequests} muted />
      </section>
    </div>
  );
}
