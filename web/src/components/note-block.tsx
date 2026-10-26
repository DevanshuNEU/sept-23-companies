import { useId, type ReactNode } from 'react';
import { Ban, Check, Clock, Copy, ExternalLink, Pencil, TriangleAlert } from 'lucide-react';
import type { CheckFix, NoteDraft, NoteFact } from '@/api';
import { CheckList } from '@/components/check-list';
import { SourceAnchor } from '@/components/source-links';
import { Button } from '@/components/ui/button';
import { Kbd } from '@/components/ui/kbd';
import { cn } from '@/lib/utils';

type Tone = 'idle' | 'warn' | 'bad';

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** The length against the API's limit, always in words: "168 of 300 characters", then how many are left past 90%. */
function charCount(chars: number, max: number): { tone: Tone; text: string } {
  const of = `${chars} of ${max} characters`;
  if (chars > max) return { tone: 'bad', text: `${of}. Over the limit by ${plural(chars - max, 'character', 'characters')}.` };
  if (chars > 0.9 * max) return { tone: 'warn', text: `${of}, ${max - chars} left` };
  return { tone: 'idle', text: of };
}

/** One line for the foot: what the checks say, with an icon beside the words. */
function checkSummary(note: NoteDraft, saving: boolean, unsaved: boolean): { tone: 'ok' | Tone; text: string } {
  if (saving) return { tone: 'idle', text: 'Saving. The checks run again before you can copy.' };
  if (unsaved) return { tone: 'idle', text: 'Your edit is not saved. Copy waits for Save.' };
  if (note.checks.length === 0) return { tone: 'idle', text: 'The checks have not run yet. Copy waits for them.' };
  const blocks = note.checks.filter((c) => c.severity === 'block').length;
  const warns = note.checks.filter((c) => c.severity === 'warn').length;
  if (blocks > 0) return { tone: 'bad', text: `${plural(blocks, 'check blocks', 'checks block')} this note` };
  if (warns > 0) return { tone: 'warn', text: plural(warns, 'warning', 'warnings') };
  return { tone: 'ok', text: `${plural(note.checks.length, 'check', 'checks')} passed` };
}

const SUMMARY_ICON = { ok: Check, warn: TriangleAlert, bad: Ban, idle: Clock } as const;
const TONE_TEXT = { ok: 'text-ok', warn: 'text-warn', bad: 'text-bad', idle: 'text-muted-foreground' } as const;

const sentence = (text: string) => (/[.!?]$/.test(text.trim()) ? text.trim() : `${text.trim()}.`);

function Fact({ fact }: { fact: NoteFact }) {
  return (
    <li className="grid grid-cols-[14px_minmax(0,1fr)] items-start gap-2">
      <ExternalLink aria-hidden className="mt-0.5 size-3.5 text-muted-foreground" />
      <span>
        {sentence(fact.text)} <SourceAnchor link={fact.source} className="text-xs text-muted-foreground" />
      </span>
    </li>
  );
}

const small = 'h-7 gap-1.5 px-2.5 text-xs has-[>svg]:px-2';
/** A button that cannot run yet keeps its focus (`aria-disabled`, not `disabled`) and looks like it waits. */
const waiting = 'aria-disabled:cursor-not-allowed aria-disabled:opacity-50';
/** A key hint on the filled button, as the mockup draws it: a translucent chip in the button's own text color. */
const onFilled = 'bg-primary-foreground/15 text-primary-foreground';

/**
 * The note to send (design 16.1, `.note-*`), on paper, the only tinted surface on Today: its length against
 * the limit, the question about his line while Claude only proposed it, the text (or `editing` in its place),
 * his line, the facts with their links before any action, the checks, then Copy and Edit.
 *
 * A note he saved himself is his reason (#9, 2026-10-26): the API stops asking (`needsYourLine` is false) and its
 * checks hold no reason line or fact rule. A line Claude proposed is never shown as "in your words", even then.
 *
 * Copy cannot run while a save is pending (`saving`: the checks are unknown until the save lands), before any
 * check ran, or while a check blocks the note, so a blocked note is never copied. Copy and Confirm line are then
 * `aria-disabled`, never `disabled`, so a focused button keeps its focus. The view's `c` and `y` keys follow the
 * same rule.
 */
export function NoteBlock({
  note,
  onCopy,
  onEdit,
  onConfirm,
  editing,
  saving = false,
  unsaved = false,
  onFix,
}: {
  note: NoteDraft;
  onCopy: () => void;
  onEdit: () => void;
  onConfirm: () => void;
  /** The editor, shown in place of the note text while he edits. */
  editing?: ReactNode;
  /** A save of this note is waiting to land: its checks are unknown. */
  saving?: boolean;
  /** An open edit differs from the saved note: Copy would copy the saved text, so it waits for Save. */
  unsaved?: boolean;
  /** Fix buttons on the checks; without it a check shows no fix. */
  onFix?: (fix: CheckFix) => void;
}) {
  const ids = useId();
  const summaryId = `${ids}-checks`;
  const count = charCount(note.chars, note.maxChars);
  const summary = checkSummary(note, saving, unsaved);
  const SummaryIcon = SUMMARY_ICON[summary.tone];
  const flagged = !saving && note.checks.some((c) => c.severity !== 'pass');
  const blocked = note.checks.some((c) => c.severity === 'block');
  // Copy runs only on a note whose checks are known and pass: not while a save is pending, not before any
  // check ran, never with a block.
  const canCopy = !saving && !unsaved && note.checks.length > 0 && !blocked;
  const asking = note.needsYourLine && !editing;
  const used = note.facts.filter((f) => f.inNote);
  const CountIcon = count.tone === 'bad' ? Ban : count.tone === 'warn' ? TriangleAlert : null;

  return (
    <section aria-labelledby={`${ids}-head`} className="rounded-lg border bg-subtle">
      <div className="flex items-center gap-2 border-b px-4 py-2.5 text-[13px]">
        <h3 id={`${ids}-head`} className="font-medium">
          Note to send
        </h3>
        <span
          data-tone={count.tone}
          className={cn('ml-auto inline-flex items-center gap-1 text-xs tabular-nums', TONE_TEXT[count.tone])}
        >
          {CountIcon ? <CountIcon aria-hidden className="size-3.5" /> : null}
          {count.text}
        </span>
      </div>

      {asking ? (
        <div className="mx-4 my-3 grid gap-2 rounded-md border bg-background px-3.5 py-3 text-[13px]">
          <b className="font-medium">Is this why you are writing to them? Confirm it or write your own.</b>
          <q className="text-sm leading-[22px] [quotes:none]">{note.reasonLine}</q>
          <span className="text-xs text-muted-foreground">Proposed by Claude from the facts below.</span>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              className={cn(small, waiting)}
              aria-keyshortcuts="y"
              aria-disabled={saving}
              onClick={() => {
                if (!saving) onConfirm();
              }}
            >
              Confirm line
              <Kbd aria-hidden className={onFilled}>
                Y
              </Kbd>
            </Button>
            <Button type="button" variant="outline" size="sm" className={small} aria-keyshortcuts="e" onClick={onEdit}>
              <Pencil aria-hidden />
              Write my own
              <Kbd aria-hidden>E</Kbd>
            </Button>
          </div>
        </div>
      ) : null}

      {editing ?? (
        <div className="bg-card">
          <p className="max-w-[62ch] px-4 py-3.5 font-serif text-[16px] leading-[1.65] whitespace-pre-wrap">{note.text}</p>
        </div>
      )}

      {/* While he edits, the line is a field in the editor, so it is not shown twice. A line Claude only proposed
          is not his words, even under a note he saved. */}
      {note.reasonState === 'proposed' || editing ? null : (
        <div className="grid gap-0.5 px-4 pb-3 text-[13px]">
          <span className="text-xs text-muted-foreground">Why them, in your words</span>
          <span>{note.reasonLine}</span>
        </div>
      )}

      <div className="grid gap-1.5 border-t px-4 py-3 text-[13px]">
        <span className="text-xs text-muted-foreground">Facts used</span>
        {used.length > 0 ? (
          <ul className="grid gap-2">
            {used.map((f, i) => (
              <Fact key={`${i}:${f.text}`} fact={f} />
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground">None. The note names no fact from a public page.</p>
        )}
      </div>

      {flagged ? (
        <div className="border-t px-4 py-3">
          <CheckList
            checks={onFix ? note.checks : note.checks.map((c) => ({ ...c, fix: null }))}
            onFix={(fix) => onFix?.(fix)}
          />
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-1.5 border-t px-4 py-2.5 text-xs text-muted-foreground">
        {/* One polite, atomic region for what decides Copy: the length and the check result. A save that lands
            and turns Copy on or off is heard. The count shows in the head; here it is for screen readers. */}
        <span
          role="status"
          aria-live="polite"
          aria-atomic="true"
          className={cn('inline-flex items-center gap-1.5', summary.tone !== 'ok' && TONE_TEXT[summary.tone])}
        >
          <SummaryIcon aria-hidden className={cn('size-3.5', TONE_TEXT[summary.tone])} />
          <span className="sr-only">{sentence(count.text)} </span>
          <span id={summaryId}>{sentence(summary.text)}</span>
        </span>
        <span className="flex-1" />
        <Button
          type="button"
          variant="outline"
          size="sm"
          className={cn(small, waiting)}
          aria-keyshortcuts="c"
          aria-describedby={canCopy ? undefined : summaryId}
          aria-disabled={!canCopy}
          onClick={() => {
            if (canCopy) onCopy();
          }}
        >
          <Copy aria-hidden />
          Copy
          <Kbd aria-hidden>C</Kbd>
        </Button>
        {note.needsYourLine || editing ? null : (
          <Button type="button" variant="ghost" size="sm" className={small} aria-keyshortcuts="e" onClick={onEdit}>
            <Pencil aria-hidden />
            Edit
            <Kbd aria-hidden>E</Kbd>
          </Button>
        )}
      </div>
    </section>
  );
}
