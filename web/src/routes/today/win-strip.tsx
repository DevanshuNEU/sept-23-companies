import { useState, useSyncExternalStore } from 'react';
import { Link } from 'react-router';
import { pollMs, queryKeys, usePageClient, type Id, type Win, type WinKind } from '@/api';
import { WinMark } from '@/components/win-mark';
import { Button } from '@/components/ui/button';
import { useHotkeys } from '@/keyboard/hotkeys';
import { ageDays, formatClockTime } from '@/lib/format';
import { cn } from '@/lib/utils';
import { useViewQuery } from '@/pending/pending';
import { quietLink } from './below-plan';
import { KeyHint } from './plan-panel';
import { formatMonthDay } from './short-dates';
import { usePlanActions } from './use-plan-actions';

/** How many wins the strip names before "and N more". */
const SHOWN = 3;
/** A referral, an interview and an offer are the most he can ask of the search (design 16.4): their line is set larger. */
const BIG: ReadonlySet<WinKind> = new Set<WinKind>(['referred', 'interview', 'offer']);

const REDUCED = '(prefers-reduced-motion: reduce)';
function subscribeMotion(onChange: () => void): () => void {
  const query = window.matchMedia(REDUCED);
  query.addEventListener('change', onChange);
  return () => query.removeEventListener('change', onChange);
}
/** Whether he asked for less motion. The global rule in the stylesheet already stops animations; this also drops the class. */
function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(subscribeMotion, () => window.matchMedia(REDUCED).matches, () => false);
}

/** "9:41" for a win from today, "Nov 9, 9:41" for an earlier one. The day comes from `asOf`, never the real clock. */
const whenOf = (win: Win, asOf: string): string =>
  ageDays(win.at, asOf) <= 0 ? formatClockTime(win.at) : `${formatMonthDay(win.at)}, ${formatClockTime(win.at)}`;

/**
 * The win banner (design 16.4): at the top of Today while any win is unseen, as the API's own line for each (newest
 * first, three at most, then "and N more"). It shows exactly what the server calls unseen, so a poll or a reload
 * changes nothing and it stays until he marks it seen (Mark seen or W, with Undo). It plays its one entrance only for
 * a win that arrives while the page is open: one already there on load, and one brought back by Undo, show complete.
 * The UI decides nothing about what is a win; a said-no reply never reaches it because the API never lists one.
 */
export function WinStrip() {
  const client = usePageClient('plan');
  const actions = usePlanActions();
  const wins = useViewQuery(queryKeys.wins, () => client.wins(), { pollMs: pollMs.default });
  const reduced = usePrefersReducedMotion();
  // The unseen wins known so far, and whether the entrance is playing. Adjusted while rendering, as the day rail does.
  const [state, setState] = useState<{ known: ReadonlySet<Id> | null; entered: boolean }>({ known: null, entered: false });

  const unseen = (wins.data?.wins ?? []).filter((w) => w.unseen).sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  if (wins.data) {
    const ids = unseen.map((w) => w.winId);
    if (state.known === null) setState({ known: new Set(ids), entered: false });
    else if (ids.some((id) => !state.known?.has(id))) setState({ known: new Set([...state.known, ...ids]), entered: true });
    else if (ids.length === 0 && state.entered) setState({ ...state, entered: false });
  }

  const markSeen = () => actions.seeWins(unseen.map((w) => w.winId), { undo: true });
  useHotkeys([{ keys: 'w', label: 'Mark wins seen', group: 'Today', enabled: unseen.length > 0, run: markSeen }]);

  if (!wins.data || unseen.length === 0) return null;
  const shown = unseen.slice(0, SHOWN);
  const more = unseen.length - shown.length;
  const big = unseen.some((w) => BIG.has(w.kind));
  return (
    <div
      role="status"
      aria-label="New wins"
      aria-live="polite"
      className={cn(
        'mx-6 mt-4 flex flex-none items-start gap-3 rounded-lg border border-ok-bd bg-ok-bg px-4 py-3',
        state.entered && !reduced && 'win-in',
      )}
    >
      <WinMark className={cn('mt-0.5', big && 'size-8')} />
      <div className="grid min-w-0 flex-1 gap-1">
        <ul className="grid gap-1">
          {shown.map((w) => (
            <li key={w.winId} className={cn('min-w-0', BIG.has(w.kind) ? 'text-base font-semibold' : 'text-sm')}>
              {w.line}
              <span className="ml-2 text-xs font-normal text-muted-foreground tabular-nums">{whenOf(w, wins.data.asOf)}</span>
            </li>
          ))}
        </ul>
        {more > 0 ? <p className="text-xs text-muted-foreground">{`and ${more} more`}</p> : null}
        <p className="text-xs">
          <Link to="/wins" className={quietLink}>
            Open the win log
          </Link>
        </p>
      </div>
      <Button type="button" variant="outline" size="sm" aria-keyshortcuts="w" onClick={markSeen}>
        Mark seen
        <KeyHint k="W" />
      </Button>
    </div>
  );
}
