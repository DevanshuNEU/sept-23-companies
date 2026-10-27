import { useEffect, useId, useRef, type ReactNode } from 'react';
import { Link } from 'react-router';
import { Check, Flag } from 'lucide-react';
import { pollMs, queryKeys, useClient, type PlanRow, type PlanView, type ShellSummary } from '@/api';
import { useAnnounce } from '@/keyboard/hotkeys';
import { formatClockTime } from '@/lib/format';
import { didToday } from '@/lib/progress';
import { useViewQuery } from '@/pending/pending';
import { TomorrowDraft } from './tomorrow-draft';

const plural = (n: number, one: string, many: string) => `${n === 1 ? one : many}`;
const link =
  'rounded-sm text-foreground underline decoration-foreground/30 underline-offset-3 transition-colors outline-none hover:decoration-foreground focus-visible:ring-[3px] focus-visible:ring-ring/70';

/** The name a row goes by in a sentence. */
const nameOf = (row: PlanRow) => row.person?.person.name ?? row.company.name;
/** A company row (company first, #9): nobody named, not an email row. */
const companyRow = (row: PlanRow) => row.person === null && row.channel !== 'email';
/** Every row is a company row: the day was a queue of companies. */
const isQueue = (plan: PlanView) => plan.rows.length > 0 && plan.rows.every(companyRow);

/** "Dana Whitfield at Resend", "Hex", "Mintlify by email". */
function Who({ row }: { row: PlanRow }) {
  if (row.channel === 'email') return <><b className="font-medium">{row.company.name}</b> by email</>;
  if (row.person === null) return <b className="font-medium">{row.company.name}</b>;
  return <><b className="font-medium">{row.person.person.name}</b> at {row.company.name}</>;
}

/** What he did on a row: "your line, request and application", "3 asked and application" on a company, or
 * "reviewed in the Outbox" for an email. */
function stepsDone(row: PlanRow): string {
  if (row.channel === 'email') return 'reviewed in the Outbox';
  if (row.queue) {
    const asked = row.steps.some((s) => s.kind === 'request' && s.at !== null) ? [`${Math.max(1, row.queue.standing.asked)} asked`] : [];
    const applied = row.steps.some((s) => s.kind === 'apply' && s.at !== null) ? ['application'] : [];
    return [...asked, ...applied].join(' and ') || 'done';
  }
  const words = row.steps.flatMap((s) => (s.at === null ? [] : [s.kind === 'request' ? 'request' : 'application']));
  const steps = words.length > 0 ? words.join(' and ') : 'done';
  return row.note?.reasonState === 'written' ? `your line, ${steps}` : steps;
}

/** The time of the row's last logged step, or null. An email row has no step to log, so it shows no time. */
function lastAt(row: PlanRow): string | null {
  // By instant, not by text: two times written with different offsets do not sort as strings.
  const last = row.steps.flatMap((s) => (s.at === null ? [] : [s.at])).sort((a, b) => Date.parse(a) - Date.parse(b)).at(-1);
  return last === undefined ? null : formatClockTime(last);
}

/** The ledger's words for a count, in lower case as in the mockup: "1 request sent", "6 requests sent". */
const LEDGER: Record<string, [one: string, many: string]> = {
  'Requests sent': ['request sent', 'requests sent'],
  'Applications sent': ['application sent', 'applications sent'],
  'Emails reviewed': ['email reviewed', 'emails reviewed'],
};
const ledgerWord = (label: string, count: number) => {
  const words = LEDGER[label];
  return words ? words[count === 1 ? 0 : 1] : label.toLowerCase();
};

/**
 * How the day ended, in the API's counts: every row is done or carried to tomorrow. A pick he said Not now to is off
 * the list (the API leaves it out of the rows and the total), so it is counted apart.
 */
function summary(plan: PlanView, carried: PlanRow[]) {
  const { done, total } = plan;
  if (isQueue(plan)) {
    // Top picks (2026-10-26): every pick is handled, reached (a request or an email there, and applied with a role)
    // or carried to tomorrow. A pick he said Not now to left the list and the bench filled its place, so the count
    // stays the day's picks; the passes are said after it.
    const handled = done + carried.length;
    const first = carried[0];
    const parts = [`${done} reached`, ...(carried.length === 0 ? [] : [carried.length === 1 && first ? `${nameOf(first)} carries to tomorrow` : `${carried.length} carry to tomorrow`])];
    const passed = plan.passed > 0 ? ` You passed on ${plan.passed} for now.` : '';
    return { sub: `${handled} of ${total} handled: ${parts.join(', ')}.${passed}`, say: `Done for today. ${handled} of ${total} handled.` };
  }
  if (carried.length === 0) {
    const sub = total === 1 ? "The one person on today's plan is done." : `All ${total} people on today's plan are done.`;
    return { sub, say: `All ${total} done for today.` };
  }
  const first = carried[0];
  const carries = carried.length === 1 && first ? `${nameOf(first)} carries to tomorrow` : `${carried.length} carry to tomorrow`;
  const counts = [`${done} of ${total} done`, `${carried.length} carried to tomorrow`];
  return {
    sub: `${done} of ${total} done. ${carries}.`,
    say: `Done for today. ${counts.join(', ')}.`,
  };
}

/** "1 reply to handle", "7 drafts to review", "5 items in Needs you": what the sidebar counts, as links. */
function waitingLinks(s: ShellSummary): { to: string; text: string }[] {
  const n = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;
  return [
    ...(s.unhandledReplies > 0 ? [{ to: '/outbox', text: n(s.unhandledReplies, 'reply to handle', 'replies to handle') }] : []),
    ...(s.draftsToReview > 0 ? [{ to: '/outbox', text: n(s.draftsToReview, 'draft to review', 'drafts to review') }] : []),
    ...(s.openReviewItems > 0 ? [{ to: '/needs-you', text: n(s.openReviewItems, 'item in Needs you', 'items in Needs you') }] : []),
  ];
}

/**
 * The plan being done is not the same as nothing waiting. This reads the same summary as the sidebar and names what
 * still waits for him, as links, in plain words. It shows nothing until the summary has loaded.
 */
function StillWaiting() {
  const client = useClient();
  const shell = useViewQuery(queryKeys.shell, () => client.shell(), { pollMs: pollMs.default });
  const ids = useId();
  if (!shell.data) return null;
  const links = waitingLinks(shell.data);
  if (links.length === 0) {
    return (
      <Section id={`${ids}-none`} title="Waiting for you">
        <p className="text-sm text-muted-foreground">Nothing is waiting in the Outbox or Needs you.</p>
      </Section>
    );
  }
  return (
    <Section id={`${ids}-waiting`} title="Still waiting for you" className="rounded-lg border px-4 py-3">
      <ul className="grid gap-1 text-sm">
        {links.map((l) => (
          <li key={l.text}>
            <Link to={l.to} className={link}>
              {l.text}
            </Link>
          </li>
        ))}
      </ul>
    </Section>
  );
}

function Section({ title, children, id, className }: { title: string; children: ReactNode; id: string; className?: string }) {
  return (
    <section aria-labelledby={id} className={`grid gap-2 ${className ?? ''}`}>
      <h3 id={id} className="text-sm font-semibold">
        {title}
      </h3>
      {children}
    </section>
  );
}

/**
 * The finish line (design 16.2): the plan ends and Today says so. It shows what he did, what carried to
 * tomorrow, tomorrow's draft and the week so far, and nothing that asks for more. The heading takes focus when the
 * panel appears, and the live region says how the day ended. Every count and line comes from the plan.
 */
export function FinishPanel({ plan }: { plan: PlanView }) {
  const ids = useId();
  const say = useAnnounce();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const done = plan.rows.filter((r) => r.state === 'done');
  const carried = plan.rows.filter((r) => r.state === 'skipped');
  const did = didToday(plan.rows);
  const { sub, say: spoken } = summary(plan, carried);

  // Focus comes to the heading only when it would otherwise be lost: on the page, or inside the work region he
  // was in. A poll that finishes the plan never takes it from a field or a dialog he is using elsewhere.
  useEffect(() => {
    const heading = headingRef.current;
    const active = document.activeElement;
    const region = heading?.closest('section') ?? null;
    if (active === null || active === document.body || (region !== null && region.contains(active))) heading?.focus();
  }, []);
  useEffect(() => {
    say(spoken);
  }, [say, spoken]);

  const { tomorrow, week } = plan;

  return (
    <div className="grid max-w-[720px] gap-6">
      <div className="grid gap-1.5 rounded-lg border bg-subtle px-6 pt-6 pb-5">
        <div className="flex items-center gap-3">
          <span aria-hidden className="grid size-7 flex-none place-items-center rounded-full bg-foreground text-background">
            <Flag className="size-[15px]" strokeWidth={2.5} />
          </span>
          <h2 ref={headingRef} tabIndex={-1} className="text-2xl leading-8 font-semibold tracking-[-0.01em] outline-none">
            Today&apos;s list is done.
          </h2>
        </div>
        <p className="max-w-[62ch] pl-10 text-base leading-6">{sub}</p>
      </div>

      <StillWaiting />

      <Section id={`${ids}-did`} title="What you did">
        {did.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing was logged today.</p>
        ) : (
          <ul className="grid">
            {did.map((d) => (
              <li key={d.label} className="grid grid-cols-[32px_minmax(0,1fr)] items-baseline gap-3 py-1.5 text-sm">
                <b className="text-right text-xl leading-7 font-semibold tabular-nums">{d.count}</b>
                <span>{ledgerWord(d.label, d.count)}</span>
              </li>
            ))}
          </ul>
        )}
        {done.length > 0 ? (
          <ul className="mt-1 border-t">
            {done.map((row) => {
              const at = lastAt(row);
              return (
                <li key={row.entryId} className="grid grid-cols-[16px_minmax(0,1fr)_auto] items-center gap-3 py-2">
                  <Check aria-hidden className="size-4 text-muted-foreground" />
                  <span className="text-sm">
                    <Who row={row} /> <span className="text-muted-foreground">{stepsDone(row)}</span>
                  </span>
                  {at ? <span className="text-xs text-muted-foreground tabular-nums">{at}</span> : null}
                </li>
              );
            })}
          </ul>
        ) : null}
      </Section>

      {carried.length > 0 ? (
        <Section id={`${ids}-carried`} title="Carried to tomorrow">
          <ul className="grid gap-1.5 text-sm">
            {carried.map((row) => (
              <li key={row.entryId}>
                <Who row={row} />.{' '}
                <span className="text-muted-foreground">
                  {companyRow(row) ? 'First on tomorrow\'s plan.' : 'First on tomorrow\'s plan, with the note as you left it.'}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {tomorrow.preview.length > 0 ? (
        <TomorrowDraft tomorrow={tomorrow} />
      ) : (
        <Section id={`${ids}-tomorrow`} title="Tomorrow">
          <p className="text-sm text-muted-foreground">{tomorrow.text}</p>
        </Section>
      )}

      <Section id={`${ids}-week`} title="This week so far" className="border-t pt-4">
        <p className="text-sm tabular-nums">
          {`${week.requests} ${plural(week.requests, 'request', 'requests')}, ${week.accepts} accepted, ${week.replies} ${plural(week.replies, 'reply', 'replies')}.`}
        </p>
        <Link to="/progress" className={`${link} justify-self-start text-sm`}>
          Open Progress
        </Link>
      </Section>
    </div>
  );
}
