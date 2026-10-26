import { useId } from 'react';
import type { PlanView, TomorrowRow } from '@/api';
import { cn } from '@/lib/utils';

/** How many people tomorrow's draft names before "and N more". */
export const DRAFT_SHOWN = 10;

/** The small channel word at the end of a row. */
function channelWord(row: TomorrowRow): string {
  if (row.findSomeone) return 'Find someone';
  return row.channel === 'email' ? 'Email' : 'LinkedIn';
}

/**
 * Tomorrow's draft: the API's dry run of the next weekday's plan (it writes nothing, and the 08:00 job builds the real
 * plan from fresh scores). The API's line, then up to ten rows in the plan's order (people first, then the companies
 * where he still has to find someone), then how many more the draft has. Every word but the heading is the API's.
 */
export function TomorrowDraft({ tomorrow, className }: { tomorrow: PlanView['tomorrow']; className?: string }) {
  const id = useId();
  const shown = tomorrow.preview.slice(0, DRAFT_SHOWN);
  const more = tomorrow.preview.length - shown.length;
  return (
    <section aria-labelledby={id} className={cn('grid gap-2', className)}>
      <div className="flex items-baseline gap-2">
        <h3 id={id} className="text-sm font-semibold">
          Tomorrow&apos;s draft
        </h3>
        <span className="text-xs text-muted-foreground">Rebuilt at 08:00</span>
      </div>
      <p className="text-sm text-muted-foreground">{tomorrow.text}</p>
      <ol className="mt-1 grid border-t text-sm">
        {shown.map((p, i) => (
          <li
            key={`${i}:${p.companyName}`}
            className="grid grid-cols-[20px_minmax(0,1fr)_auto] items-baseline gap-3 border-b py-2"
          >
            <span className="text-muted-foreground tabular-nums">{i + 1}</span>
            <span className="min-w-0 truncate">
              {p.personName ? (
                <>
                  <b className="font-medium">{p.personName}</b> at {p.companyName}
                </>
              ) : (
                <b className="font-medium">{p.companyName}</b>
              )}
              {p.roleTitle ? <span className="text-muted-foreground">{`, ${p.roleTitle}`}</span> : null}
            </span>
            <span className="text-xs text-muted-foreground">{channelWord(p)}</span>
          </li>
        ))}
      </ol>
      {more > 0 ? <p className="pl-8 text-sm text-muted-foreground">{`and ${more} more`}</p> : null}
    </section>
  );
}
