import { useEffect, useRef } from 'react';
import { Monogram } from '@/components/monogram';
import { Kbd } from '@/components/ui/kbd';
import type { BrowseCompany, PlanRow } from '@/api';
import { cn } from '@/lib/utils';

/**
 * "Use X instead of...": today's picks that can give up their slot, numbered 1 to 9 (the digit picks it, the arrows and
 * Enter work too). Nothing here is a Not now: the pick goes back to More options with no cooldown and uses no room.
 */
export function Chooser({ company, picks, at, onAt, onChoose }: { company: BrowseCompany; picks: PlanRow[]; at: number; onAt: (i: number) => void; onChoose: (row: PlanRow) => void }) {
  // Focus moves here, so keys typed after Enter reach the chooser and not the search box.
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => box.current?.focus(), []);
  return (
    <div ref={box} tabIndex={-1} role="group" aria-label={`Use ${company.company.name} instead of`} className="grid gap-2.5 rounded-lg border bg-subtle px-4 py-3 outline-none">
      <p className="text-[13px]">
        <b className="font-medium">{`Use ${company.company.name} instead of which pick?`}</b>{' '}
        <span className="text-muted-foreground">The pick goes back to More options. It is not a Not now, and it uses no room. Press a number, or use the arrows and Enter. Esc cancels.</span>
      </p>
      {picks.length === 0 ? (
        <p className="text-[13px] text-muted-foreground">Every pick is reached, done or carried, so none can give up its slot today.</p>
      ) : (
        <ol className="grid gap-1.5 sm:grid-cols-2 xl:grid-cols-3">
          {picks.slice(0, 9).map((row, i) => (
            <li key={row.entryId}>
              <button
                type="button"
                data-choice={row.entryId}
                aria-current={i === at ? 'true' : undefined}
                aria-keyshortcuts={String(i + 1)}
                onClick={() => onChoose(row)}
                onMouseEnter={() => onAt(i)}
                className={cn(
                  'flex w-full items-center gap-2.5 rounded-md border bg-background px-2.5 py-1.5 text-left text-[13px] outline-none',
                  'focus-visible:ring-[3px] focus-visible:ring-ring/70',
                  i === at ? 'border-ring bg-muted/60' : 'border-border hover:bg-muted/40',
                )}
              >
                <Kbd aria-hidden>{i + 1}</Kbd>
                <Monogram name={row.company.name} className="size-6 text-xs" />
                <span className="grid min-w-0 flex-1">
                  <span className="truncate font-medium">{row.company.name}</span>
                  <span className="truncate text-muted-foreground">{row.queue?.whyToday ?? row.reason}</span>
                </span>
              </button>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
