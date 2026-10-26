import type { FunnelStage, ProgressPeriod } from '@/api';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { formatRate, rateEnough, smallSampleNote } from '@/lib/progress';
import { cn } from '@/lib/utils';
import { periodHeading } from './period-label';

const DENOMINATOR_WORD = { requests: 'requests', replies: 'replies', accepts: 'accepts', referrals: 'referrals', interviews: 'interviews' } satisfies Record<FunnelStage['stage'], string>;

const countOf = (p: ProgressPeriod, key: FunnelStage['stage']) => p.stages.find((s) => s.stage === key)?.count ?? 0;

/** The text a bar stands for: "Accepted: 9 of 31 requests", or "Referrals: 0". */
function barLabel(s: FunnelStage): string {
  if (s.count === 0) return `${s.label}: 0`;
  if (!s.from) return `${s.label}: ${s.count}`;
  return `${s.label}: ${s.count} of ${s.from.count} ${DENOMINATOR_WORD[s.from.stage]}`;
}

/**
 * One thin monochrome bar. It grows from one baseline against `scale`, which is the period's largest count and never
 * less than the minimum sample, so a small week looks small. A non-zero count is never under 2 px; zero draws no bar.
 */
function Bar({ stage, scale }: { stage: FunnelStage; scale: number }) {
  const label = barLabel(stage);
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          data-slot="lbar"
          role="img"
          aria-label={label}
          className="relative block h-5 w-60 before:absolute before:inset-y-0 before:left-0 before:w-px before:bg-border before:content-['']"
        >
          {stage.count > 0 ? (
            <i
              style={{ width: `${(stage.count / scale) * 100}%`, minWidth: '2px' }}
              className="absolute top-1.5 left-px block h-2 rounded-r-[4px] bg-muted-foreground not-italic"
            />
          ) : null}
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" sideOffset={6}>
        {label}
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * The stages of a period as a ledger (not tiles, not a funnel graphic): the count, the rate beside its own count and
 * denominator, and a bar on one scale. A rate shows only when the API says the period has enough requests and the
 * stage's denominator has 20; otherwise it is the plain count ("2 of 9") and a note says why.
 */
export function FunnelLedger({ period, minRequests, muted = false }: { period: ProgressPeriod; minRequests: number; muted?: boolean }) {
  const largest = Math.max(0, ...period.stages.map((s) => s.count));
  const scale = Math.max(largest, minRequests, 1);
  const note = smallSampleNote(period, countOf(period, 'requests'), minRequests);
  return (
    <div>
      <div className="overflow-hidden rounded-lg border">
        <table aria-label={`Funnel for ${periodHeading(period)}`} className={cn('w-full text-[13px] leading-[18px] tabular-nums', muted && 'text-muted-foreground')}>
          <thead>
            <tr className="border-b text-xs text-muted-foreground">
              <th scope="col" className="h-8 px-3 text-left font-medium">Stage</th>
              <th scope="col" className="h-8 w-px px-3 text-right font-medium">Count</th>
              <th scope="col" className="h-8 px-3 text-left font-medium whitespace-nowrap">Rate, with its count</th>
              <th scope="col" className="h-8 px-3 text-left font-medium whitespace-nowrap">On one scale</th>
            </tr>
          </thead>
          <tbody>
            {period.stages.map((s) => {
              const zero = s.count === 0;
              const rate = s.from ? formatRate(s.count, s.from.count, rateEnough(period.enough, s.from.count)) : null;
              return (
                <tr key={s.stage} className="border-b last:border-b-0">
                  <th scope="row" className="h-10 px-3 py-2 text-left font-medium whitespace-nowrap text-foreground">{s.label}</th>
                  <td className={cn('h-10 px-3 py-2 text-right whitespace-nowrap', zero && 'text-muted-foreground')}>{s.count}</td>
                  <td className={cn('h-10 px-3 py-2 whitespace-nowrap', zero && rate === 'None yet' && 'text-muted-foreground')}>
                    {rate === null ? (
                      <span className="text-muted-foreground">Where every rate starts</span>
                    ) : (
                      <>
                        <span className={cn(zero && 'text-muted-foreground')}>{rate}</span>
                        {rate === 'None yet' ? null : <span className="text-muted-foreground">{s.from ? ` of ${DENOMINATOR_WORD[s.from.stage]}` : ''}</span>}
                      </>
                    )}
                  </td>
                  <td className="h-10 px-3 py-2">
                    <Bar stage={s} scale={scale} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {note ? <p className="mt-2.5 text-sm">{note}</p> : null}
      <p className="mt-2 text-xs text-muted-foreground">
        {largest >= minRequests
          ? `Bars share one scale: ${largest}, the largest count in this period. A rate against replies shows from 20 replies.`
          : `Bars are drawn against ${minRequests} requests, the point where rates start, so a small week looks small.`}
      </p>
    </div>
  );
}

/** What he did in the period: counts only, each beside the API's label. Zero is a plain grey 0. */
export function EffortList({ period, label, large = false }: { period: ProgressPeriod; label: string; large?: boolean }) {
  return (
    <ul aria-label={label} className="grid">
      {period.effort.map((e) => (
        <li key={e.label} className={cn('grid grid-cols-[32px_minmax(0,1fr)] items-baseline gap-3 text-sm', large ? 'py-1.5' : 'py-0.5')}>
          <b className={cn('text-right font-semibold tabular-nums', large ? 'text-xl leading-7' : 'text-sm leading-5', e.count === 0 && 'text-muted-foreground')}>{e.count}</b>
          <span>{e.label}</span>
        </li>
      ))}
    </ul>
  );
}
