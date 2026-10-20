import { ExternalLink, Pause } from 'lucide-react';
import type { OutboxDetail, ReplyKind, ThreadMessage } from '@/api';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Kbd } from '@/components/ui/kbd';
import { ageDays, formatDayTime, formatLocalTime } from '@/lib/format';
import { cn } from '@/lib/utils';
import { heldLine } from './company-actions';

export type ReplyChoice = Exclude<ReplyKind, 'unsorted'>;

// Spec 5.3, Detail: reply. "Auto-reply, not a person" is the fifth choice for the one that slips past the sender's filter.
const CHOICES: { kind: ReplyChoice; label: string }[] = [
  { kind: 'interested', label: 'Interested' },
  { kind: 'not_now', label: 'Not now' },
  { kind: 'said_no', label: 'Said no' },
  { kind: 'asked_to_stop', label: 'Asked to stop' },
];

const when = (at: string, asOf: string) => (ageDays(at, asOf) === 0 ? `Today ${formatLocalTime(at)}` : formatDayTime(at));

function Message({ m, asOf, open }: { m: ThreadMessage; asOf: string; open: boolean }) {
  const mine = m.direction === 'out';
  const kind = m.seq === null ? '' : m.seq === 0 ? 'First email' : `Follow-up ${m.seq}`;
  return (
    <details open={open} className={cn('group rounded-md border', mine ? 'bg-background' : 'border-ok-bd bg-ok-bg/40')}>
      <summary className="flex cursor-pointer items-baseline gap-2 px-3 py-2 text-[13px] outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">
        <b className="font-medium">{mine ? 'You' : m.from}</b>
        <span className="truncate text-muted-foreground">{[kind, m.subject].filter(Boolean).join(': ')}</span>
        <span className="ml-auto text-xs whitespace-nowrap text-muted-foreground tabular-nums">{when(m.at, asOf)}</span>
      </summary>
      <p className="border-t px-3 py-2.5 text-sm leading-[22px] break-words whitespace-pre-wrap text-muted-foreground">{m.body}</p>
    </details>
  );
}

/** A thread, newest first, with the oldest message folded. */
export function Thread({ messages, asOf, skip }: { messages: ThreadMessage[]; asOf: string; skip?: (m: ThreadMessage) => boolean }) {
  const shown = messages.filter((m) => !skip?.(m));
  if (shown.length === 0) return null;
  const newestFirst = [...shown].reverse();
  return (
    <div role="group" aria-label="Thread" className="grid gap-2">
      {newestFirst.map((m, i) => (
        <Message key={`${m.at}-${m.direction}-${m.seq}`} m={m} asOf={asOf} open={newestFirst.length === 1 || i < newestFirst.length - 1} />
      ))}
    </div>
  );
}

function HoldAlert({ detail }: { detail: OutboxDetail }) {
  const { item, held, gmailThreadUrl, asOf } = detail;
  const first = item.person.name.split(' ')[0] ?? item.person.name;
  const lines = held.map((h) => heldLine(h, asOf));
  const who = lines.length > 1 ? `${lines.slice(0, -1).join(', ')} and ${lines[lines.length - 1]}` : (lines[0] ?? '');
  const company = item.company.name;
  return (
    <Alert className="border-ok-bd bg-ok-bg text-ok">
      <Pause aria-hidden />
      <AlertTitle>{`${company} is on hold`}</AlertTitle>
      <AlertDescription className="text-current">
        <p>
          {`${first} replied, so nothing else goes to ${company}${lines.length > 0 ? `: ${who} ${lines.length === 1 ? 'is' : 'are'} held` : ''}. Answer in Gmail. This app never sends a reply.`}
        </p>
        {gmailThreadUrl ? (
          <Button asChild variant="outline" size="sm" className="mt-1 text-foreground">
            <a href={gmailThreadUrl} target="_blank" rel="noreferrer">
              <ExternalLink aria-hidden />
              Open in Gmail
            </a>
          </Button>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}

/** Spec 5.3, Detail: reply. Their message, what it said, the thread, and the company-level actions. Never a way to answer. */
export function ReplyDetail({
  detail,
  onReplyKind,
  onClose,
  onResume,
  canResume,
  canClose,
}: {
  detail: OutboxDetail;
  onReplyKind: (kind: ReplyChoice) => void;
  onClose: () => void;
  onResume: () => void;
  canResume: boolean;
  canClose: boolean;
}) {
  const { item, reply, thread, asOf } = detail;
  const first = item.person.name.split(' ')[0] ?? item.person.name;
  const company = item.company;
  const hold = company.heldByTouchId !== null && reply?.kind !== 'auto_reply';
  return (
    <>
      <div className="grid flex-1 auto-rows-min content-start gap-4 overflow-y-auto px-5 py-4">
        {hold ? <HoldAlert detail={detail} /> : null}
        {reply ? (
          <div className="rounded-md border border-ok-bd bg-ok-bg/40">
            <div className="flex items-baseline gap-2 px-3 py-2 text-[13px]">
              <b className="font-medium">{reply.from}</b>
              <span className="text-muted-foreground">to you</span>
              <span className="ml-auto text-xs whitespace-nowrap text-muted-foreground tabular-nums">{when(reply.at, asOf)}</span>
            </div>
            <p className="border-t px-3 py-2.5 text-sm leading-[22px] break-words whitespace-pre-wrap">{reply.text}</p>
          </div>
        ) : null}
        {reply ? (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <span className="text-sm whitespace-nowrap">{`What did ${first} say?`}</span>
            <div role="group" aria-label="Reply kind" className="inline-flex overflow-hidden rounded-md border">
              {CHOICES.map(({ kind, label }) => (
                <button
                  key={kind}
                  type="button"
                  aria-pressed={reply.kind === kind}
                  onClick={() => onReplyKind(kind)}
                  className="border-r px-3 py-1.5 text-[13px] outline-none last:border-r-0 hover:bg-muted focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-pressed:bg-foreground aria-pressed:text-background"
                >
                  {label}
                </button>
              ))}
            </div>
            <button
              type="button"
              aria-pressed={reply.kind === 'auto_reply'}
              onClick={() => onReplyKind('auto_reply')}
              className="rounded-md px-2 py-1.5 text-[13px] text-muted-foreground underline decoration-border underline-offset-3 outline-none hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-pressed:font-medium aria-pressed:text-foreground"
            >
              Auto-reply, not a person
            </button>
            <span className="basis-full text-xs text-muted-foreground">{`Said no and Asked to stop add ${first} to the no-go list.`}</span>
          </div>
        ) : null}
        <Thread messages={thread} asOf={asOf} skip={(m) => m.direction === 'in' && reply !== null && m.at === reply.at} />
      </div>
      <footer className="flex flex-none flex-wrap items-center gap-2 border-t bg-background px-5 py-3">
        {canClose ? (
          <Button type="button" onClick={onClose}>
            {`Close ${company.name} with a reason`}
            <Kbd aria-hidden>C</Kbd>
          </Button>
        ) : null}
        {canResume ? (
          <Button type="button" variant="outline" onClick={onResume}>
            {`Resume ${company.name}`}
          </Button>
        ) : null}
        {hold ? <span className="ml-auto text-xs text-muted-foreground">Held emails stay held until you resume or close.</span> : null}
      </footer>
    </>
  );
}
