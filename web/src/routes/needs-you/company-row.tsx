import { useState, type FormEvent } from 'react';
import { Lock } from 'lucide-react';
import type { ReviewFix, ReviewItem } from '@/api';
import { StatusBadge } from '@/components/status-badge';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { isTypingTarget, useHotkeys } from '@/keyboard/hotkeys';
import type { RowProps } from '@/keyboard/list-nav';
import { cn } from '@/lib/utils';
import { ActionButton } from './action-button';
import type { CompanyRow } from './groups';
import { Age, EDIT_FIELD, FIELD_HINT, FIELD_LABEL, ICON, TRIGGER, valueReady } from './review-item';

type Edit = Extract<ReviewFix, { kind: 'edit' }>;
type DetailField = 'size' | 'domain';

/** The fields a row asks for, size first, then the email domain. */
function fieldsOf(row: CompanyRow): DetailField[] {
  const have = new Set(row.members.map((m) => EDIT_FIELD[m.kind]));
  return (['size', 'domain'] as const).filter((f) => have.has(f));
}

const BUTTON: Record<string, string> = { size: 'Set size', domain: 'Set domain', 'size and domain': 'Set size and domain' };
const editLabel = (row: CompanyRow): string => BUTTON[fieldsOf(row).join(' and ')] ?? 'Set details';

/**
 * One form for everything a company is missing: a field for each open question and one Source. A field left empty
 * stays open; a field with text must be ready (a size with a digit, a domain with a dot). Save and lock stays
 * `aria-disabled` until the Source has text and at least one field is ready and none is half typed.
 */
function DetailsForm({ row, onSave, onCancel }: { row: CompanyRow; onSave: (edits: Edit[]) => void; onCancel: () => void }) {
  const fields = fieldsOf(row);
  const [values, setValues] = useState<Record<DetailField, string>>({ size: '', domain: '' });
  const [source, setSource] = useState('');
  const typed = fields.filter((f) => values[f].trim() !== '');
  const ready = source.trim() !== '' && typed.length > 0 && typed.every((f) => valueReady(f, values[f]));
  const id = `review-${row.lead.id}`;
  const name = row.lead.company.name;

  const save = () => {
    if (!ready) return;
    onSave(typed.map((f) => ({ kind: 'edit', field: f, value: values[f].trim(), source: source.trim() })));
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
    <>
      <form
        onSubmit={submit}
        onClick={(e) => e.stopPropagation()}
        aria-label={`Edit ${fields.map((f) => FIELD_LABEL[f].toLowerCase()).join(' and ')} for ${name}`}
        style={{ gridTemplateColumns: `repeat(${fields.length + 1}, minmax(0, 1fr)) auto` }}
        className="grid items-end gap-2 rounded-md border bg-card p-3"
      >
        {fields.map((f, i) => (
          <div key={f} className="grid gap-1.5">
            <Label htmlFor={`${id}-${f}`} className="text-xs">
              {FIELD_LABEL[f]}
            </Label>
            <Input
              id={`${id}-${f}`}
              autoFocus={i === 0}
              placeholder={FIELD_HINT[f]}
              value={values[f]}
              onChange={(e) => setValues((v) => ({ ...v, [f]: e.target.value }))}
            />
          </div>
        ))}
        <div className="grid gap-1.5">
          <Label htmlFor={`${id}-source`} className="text-xs">
            Source
          </Label>
          <Input id={`${id}-source`} aria-required placeholder="Where you saw it" value={source} onChange={(e) => setSource(e.target.value)} />
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
      <p className="text-xs text-muted-foreground">Fill what you know. Anything you leave empty stays open.</p>
    </>
  );
}

/** The distinct trigger badges of a row, in the order its items came. */
function kindsOf(members: ReviewItem[]): ReviewItem['kind'][] {
  return [...new Set(members.map((m) => m.kind))];
}

/**
 * One company, every size and email domain question it has open, one form and one set of buttons (#288). Leave as is
 * settles every question on the row; Mark no-go puts the company on the no-go list, which settles them too.
 */
export function CompanyDetailsRow({
  row,
  rowProps,
  editing,
  onEdit,
  onOk,
  onNoGo,
  onSave,
  onCancelEdit,
}: {
  row: CompanyRow;
  rowProps: RowProps;
  editing: boolean;
  onEdit: () => void;
  onOk: () => void;
  onNoGo: () => void;
  onSave: (edits: Edit[]) => void;
  onCancelEdit: () => void;
}) {
  const { lead, members } = row;
  const Icon = ICON[lead.kind];
  const { 'aria-selected': selected, ...rest } = rowProps;
  const oldest = members.reduce((a, b) => (b.ageDays > a.ageDays ? b : a), lead);
  return (
    <article
      {...rest}
      aria-label={lead.company.name}
      aria-current={selected || undefined}
      className={cn(
        '@container grid grid-cols-[20px_minmax(0,1fr)] gap-x-3 gap-y-1 border-b px-4 py-3.5 last:border-b-0 @4xl:grid-cols-[20px_minmax(0,1fr)_auto]',
        'data-[active=true]:bg-muted data-[active=true]:shadow-[inset_2px_0_0_var(--foreground)] dark:data-[active=true]:bg-muted/60',
      )}
    >
      <Icon aria-hidden data-icon={lead.kind} className="mt-0.5 size-4 text-warn" />
      <div className="flex flex-wrap items-center gap-2">
        <b className="font-semibold">{lead.company.name}</b>
        {kindsOf(members).map((k) => (
          <StatusBadge key={k} status={TRIGGER[k]} dot />
        ))}
        {members.some((m) => m.company.picked) ? <Badge variant="outline">Picked today</Badge> : null}
        <Age item={oldest} />
      </div>
      <div className="col-start-2 row-start-3 mt-1 flex flex-wrap items-start gap-1.5 @4xl:col-start-3 @4xl:row-span-2 @4xl:row-start-1 @4xl:mt-0">
        {!editing ? <ActionButton label={editLabel(row)} keyName="E" variant="primary" onClick={onEdit} /> : null}
        {!editing ? <ActionButton label="Leave as is" keyName="O" variant="outline" title="Mark ok" onClick={onOk} /> : null}
        <ActionButton label="Mark no-go" keyName="N" variant="quiet" onClick={onNoGo} />
      </div>
      <div className="col-start-2 grid gap-2 text-[13px]">
        {members.map((m) => (
          <p key={m.id}>{m.reason}</p>
        ))}
        {editing ? <DetailsForm row={row} onSave={onSave} onCancel={onCancelEdit} /> : null}
      </div>
    </article>
  );
}
