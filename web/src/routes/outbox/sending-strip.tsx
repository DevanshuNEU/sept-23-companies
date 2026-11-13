import { useState, type ReactNode } from 'react';
import { ChevronDown, Pause, Play } from 'lucide-react';
import type { AccountState, AccountStatus, SenderState, SendingStatus } from '@/api';
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
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { Skeleton } from '@/components/ui/skeleton';
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

// Each account's tone: green while it sends, red when it needs him (a hard block, a sign-in), grey otherwise.
const ACCOUNT_TONE: Record<AccountState, Tone> = {
  sending: 'ok',
  paused_by_you: 'idle',
  paused_hard_block: 'bad',
  signed_out: 'bad',
  reading_only: 'idle',
  not_sending_yet: 'idle',
};

/** What sends first, then what will send once it is set up, then what only reads replies. Config order within each. */
const accountRank = (a: AccountStatus) => (a.sends ? 0 : a.lane ? 1 : 2);

const RUN = '. Run: ';

/** Sender text that ends in a sign-in command ("signed out. Run: python -m ... auth --account x."), with the command
 * set as code he can copy and nothing else in the code. Text with no command reads as it is. */
export function CommandText({ text }: { text: string }) {
  const at = text.indexOf(RUN);
  if (at < 0) return <>{text}</>;
  const rest = text.slice(at + RUN.length);
  const stop = rest.indexOf('. '); // a command has no ". " in it; anything after it is its own sentence
  const command = (stop < 0 ? rest : rest.slice(0, stop)).replace(/\.$/, '');
  const after = stop < 0 ? (rest.endsWith('.') ? '.' : '') : rest.slice(stop);
  return (
    <>
      {`${text.slice(0, at)}. Run `}
      <code className="font-mono select-all">{command}</code>
      {after}
    </>
  );
}

/** One line per account: its address and what it does today ("4 of 15 today", "reading replies only"). */
function Accounts({ accounts }: { accounts: AccountStatus[] }) {
  if (accounts.length === 0) return null;
  const ordered = [...accounts].sort((a, b) => accountRank(a) - accountRank(b));
  return (
    <ul aria-label="Accounts" className="flex flex-wrap items-center gap-x-6 gap-y-1 pb-1.5 text-xs">
      {ordered.map((a) => {
        const tone = ACCOUNT_TONE[a.state];
        const red = tone === 'bad';
        return (
          <li key={a.address} data-tone={tone} title={a.pauseReason || undefined} className="flex min-w-0 items-center gap-2">
            <span aria-hidden className={cn('size-1.5 flex-none rounded-full', DOT[tone])} />
            <span className="font-medium whitespace-nowrap">{a.address}</span>
            <span className={cn('min-w-0 break-words tabular-nums', red ? 'text-bad' : 'text-muted-foreground')}>
              <CommandText text={a.text} />
            </span>
          </li>
        );
      })}
    </ul>
  );
}

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

/** The next send in one phrase for the line: "Next 10:42 ET, Mintlify", or why nothing is next. */
function nextLine(s: SendingStatus): string {
  if (!s.nextSend) return s.noneReason ?? 'Nothing is approved.';
  return `Next ${formatRecipientTime(s.nextSend.at, s.nextSend.tz)}, ${s.nextSend.companyName}`;
}

/** The rest of the sender's story, behind Details: the window, the day, the limit and the ramp, the next send. */
function Details({ s }: { s: SendingStatus }) {
  return (
    <div className="grid gap-3">
      <div className="grid gap-0.5">
        <span className="text-xs text-muted-foreground">Sender</span>
        <span className="text-sm">{senderSub(s)}</span>
      </div>
      <Cell name="Sent today" className="border-r-0 px-0 py-0">
        <span className="flex items-center gap-2 text-sm font-medium">
          <span className="text-xl font-semibold tracking-tight tabular-nums">{s.sentToday}</span>
          <span className="text-muted-foreground">{`of ${s.limitToday}`}</span>
          <Ladder s={s} />
        </span>
      </Cell>
      <Cell name="Limit" className="border-r-0 px-0 py-0">
        <span className="text-sm font-medium">{`Level ${s.rampLevel} of ${s.rampLevels.length}, ${s.limitToday} a day`}</span>
        <span className="text-xs text-muted-foreground">{limitSub(s)}</span>
      </Cell>
      <Cell name="Next send" className="border-r-0 px-0 py-0">
        {s.nextSend ? (
          <>
            <span className="text-sm font-medium tabular-nums">{formatRecipientTime(s.nextSend.at, s.nextSend.tz)}</span>
            <span className="text-xs text-muted-foreground">{`${s.nextSend.personName}, ${s.nextSend.companyName}`}</span>
          </>
        ) : (
          <span className="text-sm font-medium">{s.noneReason ?? 'Nothing is approved.'}</span>
        )}
      </Cell>
    </div>
  );
}

/** Spec 5.3: the sender in one line (state, today's count summed over the sending accounts, the next send), each
 * account on the line below it, the detail in a popover, Pause one click away (his pause holds every account). */
export function SendingStrip({ sending: s, onPause, onResume }: { sending: SendingStatus; onPause: () => void; onResume: () => void }) {
  const sender = SENDER[s.state];
  const red = sender.tone === 'bad';
  return (
    <section aria-label="Sending today" className="flex-none border-b px-4">
      <div className="flex min-h-11 flex-wrap items-center gap-x-4 gap-y-1 py-1.5">
        <div role="group" aria-label="Sender" data-tone={sender.tone} className="flex min-w-0 items-center gap-2 text-sm">
          <span aria-hidden className={cn('size-2 flex-none rounded-full', DOT[sender.tone], s.state === 'running' && 'ripple')} />
          <span className={cn('font-medium whitespace-nowrap', red && 'text-bad')}>{sender.label}</span>
          {s.state === 'paused_hard_block' ? (
            <span title={senderSub(s)} className="max-w-[44ch] truncate text-xs text-bad">
              {senderSub(s)}
            </span>
          ) : null}
        </div>
        <span className="text-sm whitespace-nowrap tabular-nums">{`${s.sentToday} of ${s.limitToday} sent`}</span>
        <span className="min-w-0 truncate text-sm text-muted-foreground tabular-nums">{nextLine(s)}</span>
        <div className="ml-auto flex items-center gap-1">
          <Popover>
            <PopoverTrigger asChild>
              <Button type="button" variant="ghost" size="sm" aria-label="Details about sending today">
                Details
                <ChevronDown aria-hidden />
              </Button>
            </PopoverTrigger>
            <PopoverContent
              align="end"
              aria-label="Details about sending today"
              className="w-80"
              // Focus goes to the panel, not its first tabbable (the ramp), whose tooltip would open on its own.
              onOpenAutoFocus={(e) => {
                e.preventDefault();
                if (e.currentTarget instanceof HTMLElement) e.currentTarget.focus();
              }}
            >
              <Details s={s} />
            </PopoverContent>
          </Popover>
          <Toggle s={s} onPause={onPause} onResume={onResume} />
        </div>
      </div>
      <Accounts accounts={s.accounts} />
    </section>
  );
}

/** Loading: the strip's line and its accounts line as blocks at their final size. */
export function SendingStripSkeleton() {
  return (
    <section aria-hidden className="flex-none border-b px-4">
      <div className="flex h-11 items-center gap-4">
        {[96, 88, 176].map((w, i) => (
          <Skeleton key={i} className="h-4" style={{ width: w }} />
        ))}
      </div>
      <div className="flex h-5 items-center gap-6 pb-1.5">
        {[220, 240, 200].map((w, i) => (
          <Skeleton key={i} className="h-3" style={{ width: w }} />
        ))}
      </div>
    </section>
  );
}
