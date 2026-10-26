import { useEffect, useId, useRef, type ReactNode } from 'react';
import { Link } from 'react-router';
import { Check, Flag } from 'lucide-react';
import type { PlanRow, PlanView } from '@/api';
import { useAnnounce } from '@/keyboard/hotkeys';
import { formatClockTime } from '@/lib/format';
import { didToday } from '@/lib/progress';
import { TomorrowDraft } from './tomorrow-draft';

const plural = (n: number, one: string, many: string) => `${n === 1 ? one : many}`;
const link =
  'rounded-sm text-foreground underline decoration-border underline-offset-3 outline-none hover:decoration-current focus-visible:ring-[3px] focus-visible:ring-ring/70';

/** The name a row goes by in a sentence. */
const nameOf = (row: PlanRow) => row.person?.person.name ?? row.company.name;

/** "Dana Whitfield at Resend", "Find someone at Hex", "Mintlify by email". */
function Who({ row }: { row: PlanRow }) {
  if (row.channel === 'email') return <><b className="font-medium">{row.company.name}</b> by email</>;
  if (row.person === null) return <><b className="font-medium">Find someone</b> at {row.company.name}</>;
  return <><b className="font-medium">{row.person.person.name}</b> at {row.company.name}</>;
}

/** What he did on a row: "your line, request and application", or "reviewed in the Outbox" for an email. */
function stepsDone(row: PlanRow): string {
  if (row.channel === 'email') return 'reviewed in the Outbox';
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
 * How the day ended, in the API's counts: every row is done or carried to tomorrow. A company removed as not a fit
 * is off the plan (the API leaves it out of the rows and the total), so it is not counted here.
 */
function summary(plan: PlanView, carried: PlanRow[]) {
  const { done, total } = plan;
  if (carried.length === 0) {
    const sub = total === 1 ? "The one person on today's plan is done." : `All ${total} people on today's plan are done.`;
    return { sub: `${sub} Nothing else is asked of you today.`, say: `All ${total} done for today.` };
  }
  const first = carried[0];
  const carries = carried.length === 1 && first ? `${nameOf(first)} carries to tomorrow` : `${carried.length} carry to tomorrow`;
  const counts = [`${done} of ${total} done`, `${carried.length} carried to tomorrow`];
  return {
    sub: `${done} of ${total} done. ${carries}, so nothing else is asked of you today.`,
    say: `Done for today. ${counts.join(', ')}.`,
  };
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
            Done for today
          </h2>
        </div>
        <p className="max-w-[62ch] pl-10 text-base leading-6">{sub}</p>
      </div>

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
                <Who row={row} />. <span className="text-muted-foreground">First on tomorrow&apos;s plan, with the note as you left it.</span>
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
