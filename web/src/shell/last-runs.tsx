import type { JobRunSummary } from '@/api';
import { StatusBadge } from '@/components/status-badge';
import { formatDayTime } from '@/lib/format';
import { jobKindLabel, jobStatus } from '@/lib/status';

const needsLook = (job: JobRunSummary) => job.status === 'failed' || job.status === 'partial' || job.late;

function Problem({ job }: { job: JobRunSummary }) {
  const counts = job.counts
    .filter((c) => c.value !== 0)
    .map((c) => `${c.label} ${c.value.toLocaleString('en-US')}`)
    .join(', ');
  return (
    <li aria-label={jobKindLabel[job.job]} className="grid gap-1 py-2.5 first:pt-0 last:pb-0">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium">{jobKindLabel[job.job]}</span>
        <span className="ml-auto flex gap-1">
          {job.late ? <StatusBadge status={jobStatus.late} /> : null}
          {job.status !== 'ok' ? <StatusBadge status={jobStatus[job.status]} /> : null}
        </span>
      </div>
      <p className="text-xs text-muted-foreground tabular-nums">{formatDayTime(job.startedAt)}</p>
      {job.error ? <p className="text-xs text-bad">{job.error}</p> : null}
      {job.late && job.expectedNextAt ? <p className="text-xs text-warn">Expected {formatDayTime(job.expectedNextAt)}</p> : null}
      {counts ? (
        <details className="text-xs text-muted-foreground">
          <summary className="w-fit cursor-pointer hover:text-foreground">Counts</summary>
          <p className="mt-1">{counts}</p>
        </details>
      ) : null}
    </li>
  );
}

function Quiet({ job }: { job: JobRunSummary }) {
  return (
    <li aria-label={jobKindLabel[job.job]} className="flex items-baseline gap-2 py-1 text-sm">
      <span>{jobKindLabel[job.job]}</span>
      <span className="ml-auto text-xs text-muted-foreground tabular-nums">
        {job.status === 'running' ? 'Running' : formatDayTime(job.startedAt)}
      </span>
    </li>
  );
}

/** The last run of every job: the ones that need a look first, in full; the rest one line each. */
export function LastRuns({ jobs }: { jobs: JobRunSummary[] }) {
  const problems = jobs.filter(needsLook);
  const fine = jobs.filter((j) => !needsLook(j));
  return (
    <div className="grid gap-4">
      <p className="text-sm font-semibold">Last runs</p>
      {jobs.length === 0 ? <p className="text-sm text-muted-foreground">Nothing has run yet</p> : null}
      {problems.length > 0 ? (
        <ul aria-label="Needs a look" className="divide-y">
          {problems.map((job) => (
            <Problem key={job.job} job={job} />
          ))}
        </ul>
      ) : null}
      {fine.length > 0 ? (
        <div className="grid gap-1">
          {problems.length > 0 ? <p className="text-xs text-muted-foreground">Ran as expected</p> : null}
          <ul aria-label="Ran as expected">
            {fine.map((job) => (
              <Quiet key={job.job} job={job} />
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
