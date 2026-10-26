import { ApiError, pollMs, queryKeys, usePageClient, type Id, type RankConfidence, type ScoreSignal, type ScoreView } from '@/api';
import { SourceAnchor } from '@/components/source-links';
import { Skeleton } from '@/components/ui/skeleton';
import { ageDays, formatClockTime, formatDay } from '@/lib/format';
import { laneLabel } from '@/lib/status';
import { cn } from '@/lib/utils';
import { useViewQuery } from '@/pending/pending';
import { TabFailed } from '../tab-boundary';
import { wholePercents } from './score-shares';

const CONFIDENCE: Record<RankConfidence, string> = { high: 'High', medium: 'Medium', low: 'Low' };

/** "1.8" or "0.9": two decimals at most, no trailing zeros. */
const plain = (n: number) => Number(n.toFixed(2)).toString();
/** "+1.80", "-0.50", "0.00". */
const signed = (n: number) => `${n > 0 ? '+' : ''}${n.toFixed(2)}`;

/** The words a bar stands for: what a signal adds or costs, in percent of everything moved. */
function barLabel(signal: ScoreSignal, percent: number): string {
  if (signal.neutral) return 'No data, scored neutral';
  const n = Math.abs(percent);
  return percent < 0 ? `Lowers the score by ${n} percent of the total` : `Adds ${n} percent of the score`;
}

/**
 * A signed bar from a centre axis: right when a signal adds, left when it costs. It scales to the largest share on
 * the page, so the biggest signal fills its half, and a non-zero share is never under 2 px. A neutral signal has no
 * bar; it says so in words.
 */
function SignedBar({ signal, percent, largest }: { signal: ScoreSignal; percent: number; largest: number }) {
  const label = barLabel(signal, percent);
  const cost = signal.share < 0;
  return (
    <span
      data-slot="sbar"
      role="img"
      aria-label={label}
      className="relative block h-5 w-[200px] before:absolute before:inset-y-0 before:left-1/2 before:w-px before:bg-border before:content-['']"
    >
      {signal.neutral ? (
        <span className="absolute top-px left-[calc(50%+10px)] text-xs whitespace-nowrap text-muted-foreground">No data, scored neutral</span>
      ) : (
        <span className={cn('absolute inset-y-0', cost ? 'right-1/2 left-0' : 'right-0 left-[calc(50%+1px)]')}>
          <i
            data-side={cost ? 'left' : 'right'}
            style={{ width: `${(Math.abs(signal.share) / largest) * 100}%`, minWidth: '2px' }}
            className={cn('absolute top-1.5 block h-2 bg-muted-foreground not-italic', cost ? 'right-0 rounded-l-[4px]' : 'left-0 rounded-r-[4px]')}
          />
        </span>
      )}
    </span>
  );
}

function ScoreSkeleton() {
  return (
    <div aria-hidden className="grid gap-3">
      <Skeleton className="h-6 w-72" />
      <Skeleton className="h-64 w-full" />
    </div>
  );
}

function ScoreTable({ view }: { view: ScoreView }) {
  const percents = wholePercents(view.signals.map((s) => s.share));
  const largest = Math.max(0, ...view.signals.map((s) => Math.abs(s.share))) || 1;
  const th = 'h-8 px-3 text-left text-xs font-medium whitespace-nowrap text-muted-foreground';
  return (
    <div className="overflow-hidden rounded-lg border">
      <table aria-label="Score by signal" className="w-full text-[13px] leading-[18px] tabular-nums">
        <thead>
          <tr className="border-b">
            <th scope="col" className={th}>Signal</th>
            <th scope="col" className={th}>Share of the score</th>
            <th scope="col" className={cn(th, 'text-right')}>Points</th>
            <th scope="col" className={cn(th, 'text-right')}>Share</th>
            <th scope="col" className={th}>Why, and where it comes from</th>
          </tr>
        </thead>
        <tbody>
          {view.signals.map((s, i) => {
            const percent = percents[i] ?? 0;
            const grey = s.neutral && 'text-muted-foreground';
            return (
              <tr key={s.name} className="border-b align-top last:border-b-0">
                <th scope="row" className="px-3 py-2.5 text-left font-medium whitespace-nowrap">{s.label}</th>
                <td className="px-3 py-2.5">
                  <SignedBar signal={s} percent={percent} largest={largest} />
                </td>
                <td className={cn('px-3 py-2.5 text-right whitespace-nowrap', grey)}>
                  {signed(s.points)}
                  <div className="text-xs text-muted-foreground">{`${plain(s.value)} × ${plain(s.weight)}`}</div>
                </td>
                <td className={cn('px-3 py-2.5 text-right whitespace-nowrap', grey)}>{`${percent}%`}</td>
                <td className="max-w-[52ch] px-3 py-2.5 text-muted-foreground">
                  {s.reason}
                  {s.source ? <SourceAnchor link={s.source} className="ml-1 text-foreground" /> : null}
                  {s.confidence === 'high' ? null : (
                    <div className="mt-0.5 text-xs">{`${CONFIDENCE[s.confidence]} confidence. ${s.basis}`}</div>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function scoredLine(view: ScoreView): string {
  const when = ageDays(view.scoredAt, view.asOf) <= 0 ? 'today' : formatDay(view.scoredAt);
  return `Scored ${when} at ${formatClockTime(view.scoredAt)}.`;
}

/**
 * Why this company ranks where it does (design 16.5): the stored score and every signal with its points, its share
 * of all the points moved, and the reason and source in words. It reads what the ranker stored and recomputes
 * nothing. It loads, fails and retries on its own, so the rest of the company page never waits on it.
 */
export function Score({ companyId }: { companyId: Id }) {
  const client = usePageClient('company');
  const query = useViewQuery(queryKeys.score(companyId), () => client.score(companyId), { pollMs: pollMs.default });
  const v = query.data;
  if (v === undefined) {
    if (query.error instanceof ApiError && query.error.code === 'not_found') {
      return <p className="px-3 py-6 text-sm text-muted-foreground">No score yet for this company.</p>;
    }
    if (query.isError) return <TabFailed name="Score" onRetry={() => void query.refetch()} />;
    return <ScoreSkeleton />;
  }
  const place = v.rank === null ? 'no rank' : `rank ${v.rank} of ${v.ofCompanies.toLocaleString('en-US')}`;
  return (
    <div className="grid max-w-[1180px] gap-3">
      <div className="grid gap-1">
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <h2 className="text-base font-semibold tabular-nums">{`Score ${v.score.toFixed(2)}, ${place}`}</h2>
          <p className="text-[13px] text-muted-foreground">
            Lanes:{' '}
            {v.lanes.map((l, i) => (
              <span key={l.lane}>
                {i > 0 ? ', ' : ''}
                <span className="text-foreground">{laneLabel[l.lane]}</span>
                {l.strongest ? ' (strongest)' : ''}
              </span>
            ))}
          </p>
        </div>
        <p className="text-[13px] text-muted-foreground">{v.rankReason}</p>
        <p className="text-[13px] text-muted-foreground">{`${CONFIDENCE[v.confidence.level]} confidence: ${v.confidence.basis}`}</p>
      </div>
      <ScoreTable view={v} />
      <div className="grid gap-1.5">
        <p className="text-xs text-muted-foreground">
          Bars grow right when a signal adds to the score and left when it costs. A share is a signal&apos;s part of all the points moved,
          added or taken away, so the shares make 100% with costs counted as their size.
        </p>
        <p className="text-[13px] text-muted-foreground">
          {`Ranker ${v.rankerVersion}. Weights live in the ranking config. A change makes a new version.`}
          {v.previous ? ` Was ${v.previous.score.toFixed(2)} under ${v.previous.rankerVersion}.` : ''} {scoredLine(v)}
        </p>
      </div>
    </div>
  );
}
