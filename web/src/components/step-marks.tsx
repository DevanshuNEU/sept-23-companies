import { Check } from 'lucide-react';
import type { IsoTime, PlanStep, StepKind } from '@/api';
import { formatClockTime } from '@/lib/format';
import { cn } from '@/lib/utils';

const WORD: Record<StepKind, string> = { request: 'Requested', apply: 'Applied' };

function Mark({ kind, at }: { kind: StepKind; at: IsoTime | null }) {
  const done = at !== null;
  return (
    <span data-done={done} className={cn('inline-flex items-center gap-1.5', done && 'text-foreground')}>
      <span
        aria-hidden
        className={cn(
          'grid size-3.5 place-items-center rounded-full border-[1.5px] border-muted-foreground',
          done && 'border-foreground bg-foreground text-background',
        )}
      >
        {done ? <Check className="size-[9px]" strokeWidth={3.5} /> : null}
      </span>
      {done ? (
        `${WORD[kind]} ${formatClockTime(at)}`
      ) : (
        <span>
          {WORD[kind]}
          <span className="sr-only">, not logged yet</span>
        </span>
      )}
    </span>
  );
}

/**
 * The row's steps (design 16.1, `.plan-steps`): Requested, then Applied when there is a role, each a filled
 * check with the time once logged and an empty mark before. The word and the time carry the state; the mark
 * is the second signal.
 */
export function StepMarks({ steps, role }: { steps: PlanStep[]; role: boolean }) {
  const at = (kind: StepKind) => steps.find((s) => s.kind === kind)?.at ?? null;
  return (
    <span role="group" aria-label="Steps" className="inline-flex items-center gap-3.5 text-[13px] text-muted-foreground">
      <Mark kind="request" at={at('request')} />
      {role ? <Mark kind="apply" at={at('apply')} /> : null}
    </span>
  );
}
