import type { IsoTime, OutboxDetail } from '@/api';
import { formatDue, formatRecipientTime } from '@/lib/format';
import { cn } from '@/lib/utils';

type Stage = { key: string; title: string; when: string; done: boolean; live: boolean };

/** "Fri Oct 30": the day of an instant in the app's zone. The clock part of the helper is cut off. */
const dayOf = (at: IsoTime) => formatRecipientTime(at, 'America/New_York', { day: true }).split(',')[0] ?? '';

/** "Tue Nov 10" for a calendar date. Read against a far-off now so it never collapses to "Due today". */
const dueDay = (on: string) => formatDue(on, '1970-01-01T12:00:00Z').replace(/^Due /, '');

function stages(firstSentAt: IsoTime | null, followups: OutboxDetail['followups'], asOf: IsoTime): Stage[] {
  const out: Stage[] = [
    {
      key: 'first',
      title: 'First email',
      when: firstSentAt ? `Sent ${dayOf(firstSentAt)}` : 'Not sent yet',
      done: firstSentAt !== null,
      live: true,
    },
  ];
  for (const seq of [1, 2, 3] as const) {
    const title = seq === 3 ? 'Follow-up 3, polite close' : `Follow-up ${seq}`;
    const f = followups.find((x) => x.seq === seq);
    if (!f) out.push({ key: String(seq), title, when: 'Not scheduled', done: false, live: false });
    else if (f.sentAt) out.push({ key: String(seq), title, when: `Sent ${dayOf(f.sentAt)}`, done: true, live: true });
    else if (f.state === 'skipped') out.push({ key: String(seq), title, when: 'Skipped', done: false, live: false });
    else {
      const due = formatDue(f.dueOn, asOf);
      out.push({ key: String(seq), title, when: due === 'Due today' ? `${due}, ${dueDay(f.dueOn)}` : due, done: false, live: true });
    }
  }
  return out;
}

/** The first email and its three follow-ups: sent ones filled, the next one outlined, the rest plain. */
export function FollowupTrack({
  firstSentAt,
  followups,
  asOf,
}: {
  firstSentAt: IsoTime | null;
  followups: OutboxDetail['followups'];
  asOf: IsoTime;
}) {
  const all = stages(firstSentAt, followups, asOf);
  const nowKey = all.find((s) => !s.done && s.live)?.key;
  return (
    <div role="group" aria-label="Follow-up schedule" className="grid grid-cols-4 gap-2">
      {all.map((s) => {
        const state = s.done ? 'done' : s.key === nowKey ? 'now' : 'next';
        return (
          <div
            key={s.key}
            data-step={state}
            className={cn(
              'grid gap-0.5 rounded-md border border-border px-2.5 py-2 text-xs',
              state === 'done' && 'bg-subtle',
              state === 'now' && 'border-foreground',
            )}
          >
            <b className="text-[13px] font-medium">{s.title}</b>
            <span className="text-muted-foreground tabular-nums">{s.when}</span>
          </div>
        );
      })}
    </div>
  );
}
