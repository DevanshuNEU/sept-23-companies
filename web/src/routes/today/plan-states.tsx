import { Link } from 'react-router';
import { CalendarX, UserPlus } from 'lucide-react';
import type { JobRunSummary, PlanView } from '@/api';
import { EmptyState, StateBanner } from '@/components/state-views';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { formatDayTime } from '@/lib/format';
import { jobKindLabel } from '@/lib/status';
import { TomorrowDraft } from './tomorrow-draft';

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

/**
 * The weekend, a plan not built yet, a failed list job: the API's words, then tomorrow's draft (who the next plan
 * would propose) when the dry run named anyone, else the API's line about the next plan alone.
 */
export function PlanNotReady({ plan }: { plan: PlanView }) {
  const { notReady, tomorrow } = plan;
  if (!notReady) return null;
  const drafted = tomorrow.preview.length > 0;
  return (
    <div className="grid justify-items-center pb-12">
      <EmptyState icon={CalendarX} title={notReady.text}>
        {!drafted && tomorrow.total > 0 ? tomorrow.text : null}
      </EmptyState>
      {drafted ? <TomorrowDraft tomorrow={tomorrow} className="-mt-4 w-full max-w-[700px] px-6" /> : null}
    </div>
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
      The plan picks the companies to reach; you find the people on LinkedIn and write your own notes. See every
      company it proposed in All proposed companies.
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
