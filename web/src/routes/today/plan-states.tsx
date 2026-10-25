import { Link } from 'react-router';
import { CalendarX, UserPlus } from 'lucide-react';
import type { JobRunSummary, PlanView } from '@/api';
import { EmptyState, StateBanner } from '@/components/state-views';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { formatDayTime } from '@/lib/format';
import { jobKindLabel } from '@/lib/status';

/** How many people tomorrow's preview names before "and N more". */
const PREVIEW = 3;

/**
 * The shared stale banner when the plan's job failed or is late, with the job's own error and name (the plan job on
 * the API, as the phase-1 Today names the daily list, now at Proposals).
 */
export function PlanJobBanner({ job }: { job: JobRunSummary | null }) {
  if (!job) return null;
  const name = jobKindLabel[job.job];
  if (job.status === 'failed') {
    return (
      <StateBanner tone="warn" title={`${name} failed ${formatDayTime(job.startedAt)}`}>
        {job.error || 'The job stopped without an error message.'}
      </StateBanner>
    );
  }
  if (job.late) {
    return (
      <StateBanner tone="warn" title={`${name} is late`}>
        {job.expectedNextAt ? `It was expected ${formatDayTime(job.expectedNextAt)}.` : 'It has not run on time.'}
      </StateBanner>
    );
  }
  return null;
}

/** Who is next: the API's line ("Monday: 7 people"), then up to three people, then how many more. */
function TomorrowPreview({ tomorrow }: { tomorrow: PlanView['tomorrow'] }) {
  const shown = tomorrow.preview.slice(0, PREVIEW);
  const more = tomorrow.total - shown.length;
  return (
    <div className="mt-2 grid justify-items-center gap-1 text-[13px]">
      <span className="font-medium text-foreground">{tomorrow.text}</span>
      {shown.length > 0 ? (
        <ul className="grid justify-items-center gap-0.5 text-muted-foreground">
          {shown.map((p, i) => (
            <li key={`${i}:${p.companyName}`}>
              {[p.personName ? `${p.personName} at ${p.companyName}` : `Find someone at ${p.companyName}`, p.roleTitle].filter(Boolean).join(', ')}
            </li>
          ))}
          {more > 0 ? <li>{`and ${more} more`}</li> : null}
        </ul>
      ) : null}
    </div>
  );
}

/** The weekend, a plan not built yet, a failed list job: the API's words, and who is next when it knows. */
export function PlanNotReady({ plan }: { plan: PlanView }) {
  const notReady = plan.notReady;
  if (!notReady) return null;
  return (
    <EmptyState icon={CalendarX} title={notReady.text}>
      {plan.tomorrow.total > 0 ? <TomorrowPreview tomorrow={plan.tomorrow} /> : null}
    </EmptyState>
  );
}

/** A plan that was built with nobody on it: who to add, and how. */
export function PlanEmpty() {
  return (
    <EmptyState
      icon={UserPlus}
      title="Nobody is on today's plan."
      action={
        <Button asChild variant="outline" size="sm">
          <Link to="/proposals">Open all proposed companies</Link>
        </Button>
      }
    >
      Add people at the companies you want to reach: copy the picks for Claude from All proposed companies and paste
      them in the side terminal. Claude adds one person per company and writes their notes.
    </EmptyState>
  );
}

/** First load only: the page at its final height (head, rail, panel), so nothing jumps when the plan lands. */
export function PlanSkeleton() {
  return (
    <div aria-hidden className="flex h-full min-h-0 flex-col">
      <div className="flex items-end gap-4 border-b px-6 pt-5 pb-4">
        <div className="grid gap-1.5">
          <Skeleton className="h-7 w-60" />
          <Skeleton className="h-4 w-32" />
        </div>
        <Skeleton className="ml-auto h-4 w-48" />
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-[320px_minmax(0,1fr)]">
        <div className="grid content-start gap-1 border-r px-3 pt-4">
          {Array.from({ length: 8 }, (_, i) => (
            <Skeleton key={i} className="h-[58px] w-full" />
          ))}
        </div>
        <div className="grid max-w-[720px] content-start gap-4 px-8 pt-5">
          <Skeleton className="h-8 w-56" />
          <Skeleton className="h-4 w-80" />
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-11 w-full" />
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-9 w-96" />
        </div>
      </div>
    </div>
  );
}
