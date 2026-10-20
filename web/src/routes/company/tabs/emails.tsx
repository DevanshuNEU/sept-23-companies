import type { CompanyView, ThreadMessage } from '@/api';
import { Badge } from '@/components/ui/badge';
import { formatDay, formatDayTime } from '@/lib/format';
import { cn } from '@/lib/utils';

function kindOf(m: ThreadMessage): string {
  if (m.direction === 'in') return 'Reply';
  if (m.seq === null || m.seq === 0) return 'First email';
  return `Follow-up ${m.seq}`;
}

function Message({ m }: { m: ThreadMessage }) {
  const reply = m.direction === 'in';
  return (
    <details className={cn('border-t first:border-t-0', reply && 'bg-ok-bg')}>
      <summary className="flex cursor-pointer items-center gap-2.5 px-3.5 py-2 text-[13px]">
        <Badge variant={reply ? 'ok' : 'outline'}>{kindOf(m)}</Badge>
        <span className="truncate">{m.subject}</span>
        <span className="ml-auto shrink-0 text-xs text-muted-foreground tabular-nums">{formatDayTime(m.at)}</span>
      </summary>
      <p className="px-3.5 pt-1 pb-3 text-[13px] whitespace-pre-wrap text-muted-foreground">{m.body}</p>
    </details>
  );
}

/** One block per touch: the person, where it stands, and every message sent or received, replies and bounces included. */
export function Emails({ view }: { view: CompanyView }) {
  if (view.touches.length === 0) return <p className="px-3 py-6 text-sm text-muted-foreground">No emails yet for this company.</p>;
  return (
    <div className="grid gap-2">
      {view.touches.map((t) => {
        const first = t.messages[0];
        const last = t.messages[t.messages.length - 1];
        return (
          <section key={t.touchId} aria-label={t.person.name} className="rounded-md border bg-card">
            <div className="flex items-center gap-2.5 border-b px-3.5 py-2.5 text-[13px]">
              <b className="font-medium">{t.person.name}</b>
              <span className="text-muted-foreground">{t.summary}</span>
              {first && last ? (
                <span className="ml-auto text-xs text-muted-foreground">
                  {first === last ? formatDay(first.at) : `${formatDay(first.at)} to ${formatDay(last.at)}`}
                </span>
              ) : null}
            </div>
            {t.messages.length === 0 ? (
              <p className="px-3.5 py-2.5 text-[13px] text-muted-foreground">Nothing sent yet.</p>
            ) : (
              t.messages.map((m, i) => <Message key={`${m.at}-${i}`} m={m} />)
            )}
          </section>
        );
      })}
    </div>
  );
}
