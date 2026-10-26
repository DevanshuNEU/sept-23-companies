import { useState, type ReactNode } from 'react';
import { Pause, Play } from 'lucide-react';
import type { SenderState, SendingStatus } from '@/api';
import { RampLadder } from '@/components/ramp-ladder';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { formatLocalTime, formatRecipientTime } from '@/lib/format';
import type { Tone } from '@/lib/status';
import { cn } from '@/lib/utils';

// The strip's words for the sender (spec 5.3, Sending strip). The sidebar footer names the activity instead.
const SENDER: Record<SenderState, { label: string; tone: Tone }> = {
  running: { label: 'Running', tone: 'ok' },
  paused_by_you: { label: 'Paused by you', tone: 'idle' },
  paused_hard_block: { label: 'Paused: hard block', tone: 'bad' },
  outside_window: { label: 'Outside the window', tone: 'idle' },
  weekend: { label: 'Weekend', tone: 'idle' },
  signed_out: { label: 'Gmail signed out', tone: 'bad' },
};

const DOT: Record<Tone, string> = { ok: 'bg-ok-dot', info: 'bg-info-dot', warn: 'bg-warn-dot', bad: 'bg-bad-dot', idle: 'bg-idle-dot' };

function Cell({ name, tone, className, children }: { name: string; tone?: Tone; className?: string; children: ReactNode }) {
  return (
    <div role="group" aria-label={name} data-tone={tone} className={cn('grid content-center gap-0.5 border-r px-4 py-2.5', className)}>
      <span className="text-xs text-muted-foreground">{name}</span>
      {children}
    </div>
  );
}

const percent = (n: number) => `${Math.round(n * 100)}%`;

function Ladder({ s }: { s: SendingStatus }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span tabIndex={0} className="inline-flex rounded-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/70">
          <RampLadder levels={s.rampLevels} level={s.rampLevel} sent={s.sentToday} limit={s.limitToday} />
        </span>
      </TooltipTrigger>
      <TooltipContent>
        <p className="font-medium">Today stays clean if</p>
        <p>{`Bounce rate, last 3 days: ${percent(s.signals.bounceRate3d)}`}</p>
        <p>{`Gmail limit or policy errors today: ${s.signals.gmailErrorsToday}`}</p>
        <p>{`Spam or stop replies today: ${s.signals.stopRepliesToday}`}</p>
      </TooltipContent>
    </Tooltip>
  );
}

function senderSub(s: SendingStatus): string {
  if (s.state === 'paused_hard_block') return s.gmailMessage ?? s.pausedReason;
  if (s.state === 'signed_out') return s.pausedReason;
  return `Weekdays ${s.window.start} to ${s.window.end}, their time`;
}

function limitSub(s: SendingStatus): string {
  if (s.lastStepDown) return `Stepped down to ${s.limitToday} at ${formatLocalTime(s.lastStepDown.at)}: ${s.lastStepDown.reason}`;
  return `${s.nextLimitIfClean} tomorrow if today stays clean`;
}

/** Resuming after a hard block shows Gmail's message first, so he reads why it stopped before it starts again. */
function ResumeAfterBlock({ s, onResume }: { s: SendingStatus; onResume: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <AlertDialog open={open} onOpenChange={setOpen}>
      <Button variant="outline" onClick={() => setOpen(true)}>
        <Play aria-hidden />
        Resume sending
      </Button>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Resume after a hard block?</AlertDialogTitle>
          <AlertDialogDescription>
            {s.pausedAt ? `Gmail stopped the sender at ${formatLocalTime(s.pausedAt)}. ` : null}
            If Gmail refuses again, sending pauses again.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <p className="rounded-md border border-bad-bd bg-bad-bg px-3 py-2 font-mono text-xs break-words text-bad">{s.gmailMessage ?? s.pausedReason}</p>
        <AlertDialogFooter>
          <AlertDialogCancel>Keep paused</AlertDialogCancel>
          <AlertDialogAction onClick={onResume}>Resume sending</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function Toggle({ s, onPause, onResume }: { s: SendingStatus; onPause: () => void; onResume: () => void }) {
  // Signed out: only signing in again fixes it, and the alert above the list says how.
  if (s.state === 'signed_out') return null;
  if (s.state === 'paused_hard_block') return <ResumeAfterBlock s={s} onResume={onResume} />;
  if (s.state === 'paused_by_you') {
    return (
      <Button variant="outline" onClick={onResume}>
        <Play aria-hidden />
        Resume sending
      </Button>
    );
  }
  return (
    <Button variant="outline" onClick={onPause}>
      <Pause aria-hidden />
      Pause sending
    </Button>
  );
}

/** Spec 5.3: the one place the sender's state, today's count, the limit and the next send live. */
export function SendingStrip({ sending: s, onPause, onResume }: { sending: SendingStatus; onPause: () => void; onResume: () => void }) {
  const sender = SENDER[s.state];
  const red = sender.tone === 'bad';
  return (
    <section aria-label="Sending today" className="flex flex-none items-stretch border-b">
      <Cell name="Sender" tone={sender.tone} className="max-w-[340px] min-w-[200px]">
        <span className={cn('flex items-center gap-2 text-sm font-medium', red && 'text-bad')}>
          <span aria-hidden className={cn('size-2 flex-none rounded-full', DOT[sender.tone], s.state === 'running' && 'animate-pulse')} />
          {sender.label}
        </span>
        <span title={senderSub(s)} className={cn('line-clamp-2 text-xs', red ? 'text-bad' : 'text-muted-foreground')}>
          {senderSub(s)}
        </span>
      </Cell>
      <Cell name="Sent today">
        <span className="flex items-center gap-2 text-sm font-medium">
          <span className="text-xl font-semibold tracking-tight tabular-nums">{s.sentToday}</span>
          <span className="text-muted-foreground">{`of ${s.limitToday}`}</span>
          <Ladder s={s} />
        </span>
      </Cell>
      <Cell name="Limit">
        <span className="text-sm font-medium">{`Level ${s.rampLevel} of ${s.rampLevels.length}, ${s.limitToday} a day`}</span>
        <span className="text-xs text-muted-foreground">{limitSub(s)}</span>
      </Cell>
      <Cell name="Next send">
        {s.nextSend ? (
          <>
            <span className="text-sm font-medium tabular-nums">{formatRecipientTime(s.nextSend.at, s.nextSend.tz)}</span>
            <span className="text-xs text-muted-foreground">{`${s.nextSend.personName}, ${s.nextSend.companyName}`}</span>
          </>
        ) : (
          <span className="text-sm font-medium">{s.noneReason ?? 'Nothing is approved.'}</span>
        )}
      </Cell>
      <div className="ml-auto flex items-center px-4">
        <Toggle s={s} onPause={onPause} onResume={onResume} />
      </div>
    </section>
  );
}

/** Loading: the strip's cells as blocks at their final size. */
export function SendingStripSkeleton() {
  return (
    <section aria-hidden className="flex h-[61px] flex-none items-stretch border-b">
      {[200, 150, 190, 170].map((w, i) => (
        <div key={i} className="grid content-center gap-1.5 border-r px-4" style={{ width: w }}>
          <span className="h-3 w-16 animate-pulse rounded bg-muted" />
          <span className="h-4 w-24 animate-pulse rounded bg-muted" />
        </div>
      ))}
    </section>
  );
}
