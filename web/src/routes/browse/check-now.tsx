import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { TriangleAlert } from 'lucide-react';
import { queryKeys, usePageClient, type CheckNowResult, type CheckStatus } from '@/api';
import { Button } from '@/components/ui/button';
import { Kbd } from '@/components/ui/kbd';
import { useAnnounce } from '@/keyboard/hotkeys';
import { usePendingWrite } from '@/pending/pending';

/**
 * Check now (#194, design 4.3): one click asks the crawl job to read every Greenhouse and Ashby board. The page reads
 * how it is going every second while the check waits or runs, and says it in one line under the header.
 */

/** How often the status is read while a check waits or runs (progress writes never move the pulse). */
const CHECK_POLL_MS = 1000;

const QUEUED = 'Waiting for the crawler. The check starts when it is free.';
// The request is recorded but the crawl job was not started: outside the live checkout, or the start failed.
const NOT_LIVE = 'Check asked for. It starts with the next crawl.';
const STARTING = 'Starting the check';

/** The ATS names the crawler uses, as people write them. */
const ATS_NAMES: Record<string, string> = {
  greenhouse: 'Greenhouse', ashby: 'Ashby', lever: 'Lever', smartrecruiters: 'SmartRecruiters', workday: 'Workday', workable: 'Workable',
  oracle_orc: 'Oracle', rippling: 'Rippling', breezy: 'Breezy', pinpoint: 'Pinpoint', teamtailor: 'Teamtailor', recruitee: 'Recruitee',
  bamboohr: 'BambooHR', jazzhr: 'JazzHR',
};

const count = (n: number) => n.toLocaleString('en-US');
const busy = (state: CheckStatus['state'] | undefined) => state === 'queued' || state === 'running';

/** "42 s", "3 min 10 s", "3 min", "1 h 5 min". */
function tookText(seconds: number): string {
  const s = Math.max(Math.round(seconds), 0);
  if (s < 60) return `${s} s`;
  if (s < 3600) return s % 60 === 0 ? `${Math.floor(s / 60)} min` : `${Math.floor(s / 60)} min ${s % 60} s`;
  const min = Math.floor((s % 3600) / 60);
  return min === 0 ? `${Math.floor(s / 3600)} h` : `${Math.floor(s / 3600)} h ${min} min`;
}

/** "Greenhouse paused: too many failed requests" (the breaker opens on errors in a row, not only on a 429), "Greenhouse
 * and Ashby paused: ...", or null when every host is read. */
function pausedText(hosts: readonly string[]): string | null {
  if (hosts.length === 0) return null;
  const names = hosts.map((h) => ATS_NAMES[h] ?? h);
  const list = names.length === 1 ? names[0] : `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`;
  return `${list} paused: too many failed requests`;
}

/** The line for a running or finished check: "Checked 1,240 of 4,013 boards, 3 new"; "Checked 4,013 boards in 3 min 10 s. 6 new." */
function progressText(s: CheckStatus): string {
  if (s.state === 'running') return s.total === 0 ? STARTING : `Checked ${count(s.checked)} of ${count(s.total)} boards, ${count(s.new)} new`;
  const boards = s.checked >= s.total ? count(s.checked) : `${count(s.checked)} of ${count(s.total)}`;
  const ms = s.startedAt && s.finishedAt ? Date.parse(s.finishedAt) - Date.parse(s.startedAt) : NaN;
  const took = Number.isFinite(ms) ? ` in ${tookText(ms / 1000)}` : '';
  return `Checked ${boards} boards${took}. ${s.new > 0 ? `${count(s.new)} new.` : 'Nothing new.'}`;
}

export interface CheckLine { text: string; paused: string | null }

/**
 * The check's state for the page: `ask` sends Check now (once at a time, through the page's write helpers, so the
 * request id is its idempotency key), and `line` is what to say, or null. A finished check shows only when this visit
 * saw it wait or run, or asked for it, so an old one never reads as news. When a check finds postings, or ends, the job
 * list is read again at once.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function useCheckNow(): { ask: () => void; line: CheckLine | null } {
  const client = usePageClient('plan');
  const queryClient = useQueryClient();
  const write = usePendingWrite();
  const announce = useAnnounce();
  const status = useQuery({
    queryKey: queryKeys.checkStatus,
    queryFn: () => client.checkStatus(),
    refetchInterval: (q) => (busy(q.state.data?.state) ? CHECK_POLL_MS : false),
  });
  const s = status.data;
  // 'asking' from the click until the answer is in and the status has been read after it.
  const [asked, setAsked] = useState<CheckNowResult['state'] | 'asking' | null>(null);
  const [watching, setWatching] = useState(false);
  if (!watching && busy(s?.state)) setWatching(true);

  const ask = () => {
    // A click while a check waits or runs keeps its line; any other click says it is starting until the answer is in.
    if (!busy(s?.state)) setAsked('asking');
    setWatching(true);
    write({
      id: 'jobs-check-now',
      label: STARTING,
      afterCommit: STARTING,
      patches: [],
      immediate: true,
      silent: true,
      commit: (o) =>
        client.checkNow(o).then(
          async (res) => {
            await queryClient.invalidateQueries({ queryKey: queryKeys.checkStatus, exact: true });
            setAsked(res.state);
            if (res.state === 'not_live') announce(NOT_LIVE);
            else if (res.state === 'started') announce('Check started.');
          },
          (err: unknown) => {
            setAsked(null);
            throw err;
          },
        ),
    });
  };

  // New rows reach the list as the check finds them, and once more when it ends.
  const found = s?.state === 'running' || s?.state === 'done' ? s.new : 0;
  const last = useRef({ state: s?.state, found });
  useEffect(() => {
    const before = last.current;
    last.current = { state: s?.state, found };
    const ended = s?.state === 'done' && busy(before.state);
    if (ended || (s?.state === 'running' && found > before.found)) void queryClient.invalidateQueries({ queryKey: queryKeys.jobsAll });
    if (ended && s) announce(progressText(s));
  }, [s, found, queryClient, announce]);

  let line: CheckLine | null = null;
  if (asked === 'asking') line = { text: STARTING, paused: null };
  else if (s?.state === 'running') line = { text: progressText(s), paused: pausedText(s.pausedHosts) };
  else if (s?.state === 'queued' || (asked === 'not_live' && s?.state !== 'done')) line = { text: asked === 'not_live' ? NOT_LIVE : QUEUED, paused: null };
  else if (s?.state === 'done' && watching) line = { text: progressText(s), paused: pausedText(s.pausedHosts) };
  return { ask, line };
}

/** The header's Check now button; C does the same. */
export function CheckNowButton({ onClick }: { onClick: () => void }) {
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      className="h-8 gap-2 px-3 text-xs max-sm:h-11"
      aria-keyshortcuts="c"
      title="Read every Greenhouse and Ashby board now (C)"
      onClick={onClick}
    >
      Check now
      <Kbd aria-hidden>C</Kbd>
    </Button>
  );
}

/** The one line under the header: what the check is doing, and a host it had to pause, in amber. */
export function CheckLineView({ line }: { line: CheckLine }) {
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-0.5" aria-live="off">
      <span>{line.text}</span>
      {line.paused ? (
        <span className="inline-flex items-center gap-1 text-warn">
          <TriangleAlert aria-hidden className="size-3.5" />
          {line.paused}
        </span>
      ) : null}
    </p>
  );
}
