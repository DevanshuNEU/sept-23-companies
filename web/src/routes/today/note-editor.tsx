import { useId } from 'react';
import type { NoteDraft } from '@/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Kbd } from '@/components/ui/kbd';
import { Textarea } from '@/components/ui/textarea';
import { isMac } from '@/keyboard/hotkeys';
import { cn } from '@/lib/utils';

/** What he is typing for one note: the text and his line. Kept by the view, so it survives moving rows. */
export type NoteEdit = { text: string; line: string };

/**
 * The note editor (design 16.1, `.note-edit`), shown in the note block in place of the text. While Claude only
 * proposed the line, the proposed line stays in view, read-only, above the fields, so he can write his own beside
 * it. The count is live; the saved count and checks stay in the block until the save lands.
 */
export function NoteEditor({
  note,
  edit,
  name,
  fieldId,
  onChange,
  onSave,
  onCancel,
}: {
  note: NoteDraft;
  edit: NoteEdit;
  /** The person the note is to, for the field's name. */
  name: string;
  /** The text field's id: the view focuses it when he starts an edit, never on a remount. */
  fieldId?: string;
  onChange: (edit: NoteEdit) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const ids = useId();
  const over = edit.text.length > note.maxChars;
  return (
    <div className="grid gap-3 px-4 py-3">
      {note.reasonState === 'proposed' ? (
        <div data-proposed className="grid gap-0.5 rounded-md border bg-background px-3 py-2 text-[13px]">
          <span className="text-xs text-muted-foreground">Claude proposed</span>
          <q className="[quotes:none]">{note.reasonLine}</q>
        </div>
      ) : null}
      <Textarea
        id={fieldId}
        aria-label={`Note to ${name}`}
        value={edit.text}
        onChange={(e) => onChange({ ...edit, text: e.target.value })}
        className="min-h-[120px] max-w-[62ch] bg-card font-serif text-[16px] leading-[1.65] md:text-[16px]"
      />
      <div className="grid gap-1">
        <label htmlFor={`${ids}-line`} className="text-xs text-muted-foreground">
          Why them, in your words
        </label>
        <Input
          id={`${ids}-line`}
          value={edit.line}
          onChange={(e) => onChange({ ...edit, line: e.target.value })}
          className="h-8 bg-background text-[13px] md:text-[13px]"
        />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">Edits are kept if you move to another person.</span>
        <span className="flex-1" />
        <span className={cn('text-xs tabular-nums', over ? 'text-bad' : 'text-muted-foreground')}>
          {`${edit.text.length} of ${note.maxChars} characters`}
        </span>
        <Button type="button" variant="ghost" size="sm" className="h-7 px-2.5 text-xs" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="button" size="sm" className="h-7 gap-1.5 px-2.5 text-xs" aria-keyshortcuts="Meta+S Control+S" onClick={onSave}>
          Save
          <Kbd aria-hidden className="bg-primary-foreground/15 text-primary-foreground">
            {isMac() ? '⌘S' : 'Ctrl S'}
          </Kbd>
        </Button>
      </div>
    </div>
  );
}
