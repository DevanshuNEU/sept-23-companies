import { useState, type FormEvent, type ReactNode } from 'react';
import { Archive, CircleHelp, Clock, Flag, Lock, Mail, RefreshCw, User, type LucideIcon } from 'lucide-react';
import type { IsoTime, ReviewFix, ReviewItem, ReviewKind } from '@/api';
import { RunStrip } from '@/components/run-strip';
import { StatusBadge } from '@/components/status-badge';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Kbd } from '@/components/ui/kbd';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { isTypingTarget, useHotkeys } from '@/keyboard/hotkeys';
import type { RowProps } from '@/keyboard/list-nav';
import { formatDay, formatLocalTime } from '@/lib/format';
import type { Status } from '@/lib/status';
import { cn } from '@/lib/utils';
import { safeHref } from '@/lib/safe-href';

export type EditField = Extract<ReviewFix, { kind: 'edit' }>['field'];

/** The trigger badge, as the mockup words it. */
// eslint-disable-next-line react-refresh/only-export-components
export const TRIGGER = {
  domain_missing: { label: 'Missing email domain', tone: 'bad' },
  size_unknown: { label: 'Unknown size', tone: 'warn' },
  size_guess: { label: 'Size is a guess', tone: 'warn' },
  sponsorship_unclear: { label: 'Unclear sponsorship', tone: 'warn' },
  board_unreadable: { label: 'Board unreadable', tone: 'bad' },
  board_empty: { label: 'Board shows zero jobs', tone: 'warn' },
  flagged: { label: 'Flagged', tone: 'warn' },
} satisfies Record<ReviewKind, Status>;

const ICON: Record<ReviewKind, LucideIcon> = {
  domain_missing: Mail,
  size_unknown: User,
  size_guess: User,
  sponsorship_unclear: CircleHelp,
  board_unreadable: RefreshCw,
  board_empty: Archive,
  flagged: Flag,
};

/** The field Edit sets for each trigger. A flag has none: mark it ok or no-go. */
// eslint-disable-next-line react-refresh/only-export-components
export const EDIT_FIELD: Record<ReviewKind, EditField | null> = {
  domain_missing: 'domain',
  size_unknown: 'size',
  size_guess: 'size',
  sponsorship_unclear: 'sponsorship',
  board_unreadable: 'board_url',
  board_empty: 'board_url',
  flagged: null,
};

const FIELD_LABEL: Record<EditField, string> = { domain: 'Email domain', size: 'Size', sponsorship: 'Sponsorship', board_url: 'Board URL' };
const FIELD_HINT: Record<Exclude<EditField, 'sponsorship'>, string> = {
  domain: 'company.com',
  size: 'About 45, or 11 to 50',
  board_url: 'https://jobs.ashbyhq.com/company',
};
const EDIT_BUTTON: Record<EditField, string> = { domain: 'Set domain', size: 'Set size', sponsorship: 'Set sponsorship', board_url: 'Edit board' };
const SPONSORSHIP = ['Yes', 'No', 'Unclear'] as const;

/** Past this many days an item reads "Waiting N days" in amber instead of its opening day. */
const STALE_DAYS = 7;

function Age({ item }: { item: ReviewItem }) {
  if (item.ageDays > STALE_DAYS) {
    return (
      <Badge variant="warn">
        <Clock aria-hidden />
        {`Waiting ${item.ageDays} days`}
      </Badge>
    );
  }
  const text = item.ageDays === 0 ? `Opened today ${formatLocalTime(item.openedAt)}` : `Opened ${formatDay(item.openedAt)}`;
  return <span className="text-xs text-muted-foreground">{text}</span>;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

/** "Oct 19 ok, then 5 errors" or "Empty on Thu Nov 5 and Mon Nov 9": the run of the latest result. */
function readsSummary(reads: ReviewItem['reads'], lastOkAt: IsoTime | null): string {
  const last = reads.at(-1);
  if (!last || last.result === 'ok') return lastOkAt ? `Last read ok ${formatDay(lastOkAt)}` : '';
  let start = reads.length - 1;
  while (start > 0 && reads[start - 1]?.result === last.result) start -= 1;
  const run = reads.slice(start);
  if (last.result === 'empty') return `Empty on ${run.map((r) => formatDay(r.at)).join(' and ')}`;
  const errors = plural(run.length, 'error');
  return lastOkAt ? `${formatDay(lastOkAt).replace(/^\w+ /, '')} ok, then ${errors}` : errors;
}

function Evidence({ item }: { item: ReviewItem }) {
  const parts: ReactNode[] = [];
  if (item.reopened) {
    parts.push(
      <p key="reopened" className="text-xs font-medium text-warn">{`Reopened: ${item.reopened.changed}, ${item.reopened.previous}`}</p>,
    );
  }
  if (item.quote) {
    const q = item.quote;
    parts.push(
      <p key="quote" className="border-l-2 pl-2.5 text-muted-foreground">{`"${q.text}"`}</p>,
      <p key="posting" className="text-xs text-muted-foreground">
        {`${q.postingTitle} posting, first seen ${formatDay(q.firstSeenAt)}. `}
        {safeHref(q.url) ? (
          <a className="text-foreground underline underline-offset-2" href={safeHref(q.url)} target="_blank" rel="noreferrer">
            Open the posting
          </a>
        ) : null}
      </p>,
    );
  }
  if (item.reads.length > 0) {
    parts.push(
      <p key="reads" className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>{`Last ${plural(item.reads.length, 'read')}`}</span>
        <RunStrip reads={item.reads} />
        <span>{readsSummary(item.reads, item.lastOkAt)}</span>
      </p>,
    );
  }
  return <>{parts}</>;
}

function ActionButton({ label, keyName, variant, onClick }: { label: string; keyName: string; variant: 'outline' | 'ghost'; onClick: () => void }) {
  return (
    <Button
      type="button"
      size="sm"
      variant={variant}
      aria-keyshortcuts={keyName.toLowerCase()}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
    >
      {label}
      <Kbd>{keyName}</Kbd>
    </Button>
  );
}

/**
 * The inline edit form: the field, a required source, and Save and lock. Mounted only while open, so it
 * starts empty. Save and lock stays `aria-disabled` until both have text. Sponsorship "No" is the no-go
 * outcome, so it goes to the no-go dialog with the source carried over.
 */
function EditForm({
  item,
  field,
  onSave,
  onNoGo,
  onCancel,
}: {
  item: ReviewItem;
  field: EditField;
  onSave: (fix: Extract<ReviewFix, { kind: 'edit' }>) => void;
  onNoGo: (source: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState('');
  const [source, setSource] = useState('');
  const ready = value.trim() !== '' && source.trim() !== '';
  const id = `review-${item.id}`;

  const save = () => {
    if (!ready) return;
    if (field === 'sponsorship' && value === 'No') onNoGo(source.trim());
    else onSave({ kind: 'edit', field, value: value.trim(), source: source.trim() });
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save();
  };

  const group = 'Needs you';
  useHotkeys([
    { keys: 'mod+enter', label: 'Save and lock', group, run: save },
    {
      keys: 'escape',
      label: 'Leave the field, then close the form',
      group,
      run: () => {
        if (document.querySelector('[role="dialog"], [role="alertdialog"]')) return;
        const active = document.activeElement;
        if (active instanceof HTMLElement && isTypingTarget(active)) active.blur();
        else onCancel();
      },
    },
  ]);

  return (
    <form
      onSubmit={submit}
      onClick={(e) => e.stopPropagation()}
      aria-label={`Edit ${FIELD_LABEL[field].toLowerCase()} for ${item.company.name}`}
      className="grid grid-cols-[160px_minmax(0,1fr)_auto] items-end gap-2 rounded-md border bg-card p-3"
    >
      <div className="grid gap-1.5">
        {field === 'sponsorship' ? (
          <>
            <span id={`${id}-field`} className="text-xs font-medium">
              {FIELD_LABEL[field]}
            </span>
            <RadioGroup
              aria-labelledby={`${id}-field`}
              value={value}
              onValueChange={setValue}
              className="flex h-9 items-center gap-3"
            >
              {SPONSORSHIP.map((v) => (
                <div key={v} className="flex items-center gap-1.5">
                  <RadioGroupItem id={`${id}-sp-${v}`} value={v} autoFocus={v === 'Yes'} />
                  <Label htmlFor={`${id}-sp-${v}`} className="font-normal">
                    {v}
                  </Label>
                </div>
              ))}
            </RadioGroup>
          </>
        ) : (
          <>
            <Label htmlFor={`${id}-value`} className="text-xs">
              {FIELD_LABEL[field]}
            </Label>
            <Input id={`${id}-value`} autoFocus placeholder={FIELD_HINT[field]} value={value} onChange={(e) => setValue(e.target.value)} />
          </>
        )}
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor={`${id}-source`} className="text-xs">
          Source
        </Label>
        <Input
          id={`${id}-source`}
          aria-required
          placeholder="Where you saw it"
          value={source}
          onChange={(e) => setSource(e.target.value)}
        />
      </div>
      <div className="flex gap-1.5">
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" aria-disabled={!ready} aria-keyshortcuts="Meta+Enter Control+Enter" className="aria-disabled:opacity-50">
          <Lock aria-hidden />
          Save and lock
        </Button>
      </div>
    </form>
  );
}

/** One review item: icon, company, badges, age, the plain sentence, the evidence, and the actions on the right. */
export function ReviewItemRow({
  item,
  row,
  editing,
  onEdit,
  onOk,
  onNoGo,
  onSave,
  onCancelEdit,
}: {
  item: ReviewItem;
  row: RowProps;
  editing: boolean;
  onEdit: () => void;
  onOk: () => void;
  onNoGo: (source?: string) => void;
  onSave: (fix: Extract<ReviewFix, { kind: 'edit' }>) => void;
  onCancelEdit: () => void;
}) {
  const trigger = TRIGGER[item.kind];
  const Icon = ICON[item.kind];
  const field = EDIT_FIELD[item.kind];
  const { 'aria-selected': selected, ...rest } = row;
  return (
    <article
      {...rest}
      aria-label={item.company.name}
      aria-current={selected || undefined}
      className={cn(
        'grid grid-cols-[20px_minmax(0,1fr)_auto] gap-x-3 gap-y-1 border-b px-4 py-3.5 last:border-b-0',
        'data-[active=true]:bg-muted data-[active=true]:shadow-[inset_2px_0_0_var(--foreground)] dark:data-[active=true]:bg-muted/60',
      )}
    >
      <Icon aria-hidden className={cn('mt-0.5 size-4', trigger.tone === 'bad' ? 'text-bad' : 'text-warn')} />
      <div className="flex flex-wrap items-center gap-2">
        <b className="font-semibold">{item.company.name}</b>
        <StatusBadge status={trigger} dot />
        {item.company.picked ? <Badge variant="outline">Picked today</Badge> : null}
        <Age item={item} />
      </div>
      <div className="col-start-3 row-span-2 row-start-1 flex items-start gap-1.5">
        {field && !editing ? <ActionButton label={EDIT_BUTTON[field]} keyName="E" variant="outline" onClick={onEdit} /> : null}
        {!editing ? <ActionButton label="Mark ok" keyName="O" variant="ghost" onClick={onOk} /> : null}
        <ActionButton label="Mark no-go" keyName="N" variant="ghost" onClick={() => onNoGo()} />
      </div>
      <div className="col-start-2 grid gap-2 text-[13px]">
        <p>{item.reason}</p>
        <Evidence item={item} />
        {editing && field ? <EditForm item={item} field={field} onSave={onSave} onNoGo={(s) => onNoGo(s)} onCancel={onCancelEdit} /> : null}
      </div>
    </article>
  );
}
