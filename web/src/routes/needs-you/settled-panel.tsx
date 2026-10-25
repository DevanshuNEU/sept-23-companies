import { useEffect, useId, useRef } from 'react';
import { Link } from 'react-router';
import { CircleCheck, Lock, Send } from 'lucide-react';
import type { IsoTime, ResolvedReview } from '@/api';
import { Button } from '@/components/ui/button';
import { Kbd, KbdGroup } from '@/components/ui/kbd';
import { useAnnounce } from '@/keyboard/hotkeys';
import { formatDay, formatLocalTime } from '@/lib/format';

const NEW_ITEMS = 'New items appear after each board read and when you pick a company.';

/**
 * The finish line of Needs you (design 5.4, Settled panel): shown in place of the list when nothing is open and he
 * settled something today. It says how many, lists each with what he did, says when the boards are read next, and
 * points at the Outbox when drafts wait there. The heading takes focus when it would otherwise be lost (the row he
 * settled is gone), and the live region says "All settled."
 */
export function SettledPanel({
  settledToday,
  today,
  nextReadAt,
  drafts,
}: {
  settledToday: number;
  today: ResolvedReview[];
  nextReadAt: IsoTime | null;
  drafts: number;
}) {
  const id = useId();
  const say = useAnnounce();
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const heading = headingRef.current;
    const active = document.activeElement;
    const region = heading?.closest('section') ?? null;
    if (active === null || active === document.body || (region !== null && region.contains(active))) heading?.focus();
    say('All settled.');
  }, [say]);

  const next = nextReadAt ? `Boards are read again ${formatDay(nextReadAt)} at ${formatLocalTime(nextReadAt)}. ` : '';
  return (
    <section
      aria-labelledby={id}
      data-slot="card"
      className="mt-6 grid grid-cols-[20px_minmax(0,1fr)] gap-x-3 gap-y-1 rounded-lg border bg-card px-6 py-5 text-card-foreground shadow-sm"
    >
      <CircleCheck aria-hidden className="mt-0.5 size-5 text-muted-foreground" />
      <div className="grid min-w-0 gap-4">
        <div>
          <h2 id={id} ref={headingRef} tabIndex={-1} className="text-lg font-semibold outline-none">
            All settled.
          </h2>
          <p className="text-sm text-muted-foreground">{`You settled ${settledToday} today.`}</p>
        </div>
        {today.length > 0 ? (
          <ul className="grid border-t">
            {today.map((r) => (
              <li
                key={r.id}
                className="grid min-h-9 grid-cols-[148px_minmax(0,1fr)_auto_48px] items-center gap-3 border-b py-2 text-[13px]"
              >
                <b className="truncate font-medium">{r.companyName}</b>
                <span>{r.note}</span>
                <span className="inline-flex items-center gap-1.5 text-xs whitespace-nowrap text-muted-foreground">
                  {r.locked ? (
                    <>
                      <Lock aria-hidden className="size-3.5" />
                      Locked
                    </>
                  ) : null}
                </span>
                <span className="text-right text-xs text-muted-foreground tabular-nums">{formatLocalTime(r.at)}</span>
              </li>
            ))}
          </ul>
        ) : null}
        <p className="text-sm text-muted-foreground">{`${next}${NEW_ITEMS}`}</p>
        {drafts > 0 ? (
          <div className="flex flex-wrap items-center gap-3">
            <Button asChild variant="outline" size="sm">
              <Link to="/outbox" aria-keyshortcuts="g o">
                <Send aria-hidden />
                Open the Outbox
                <KbdGroup>
                  <Kbd>G</Kbd>
                  <Kbd>O</Kbd>
                </KbdGroup>
              </Link>
            </Button>
            <span className="text-sm text-muted-foreground">{`${drafts} ${drafts === 1 ? 'draft waits' : 'drafts wait'} for review there.`}</span>
          </div>
        ) : null}
      </div>
    </section>
  );
}
