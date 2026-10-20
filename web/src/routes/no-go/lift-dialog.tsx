import { useRef, useState, type FormEvent } from 'react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { entryName, type CompanyEntry } from './no-go-table';

/** What the dialog says and does for each kind of row. */
function copyFor(row: CompanyEntry): { title: string; body: string; action: string } {
  const name = entryName(row);
  if (row.kind === 'removed') {
    return {
      title: `Bring back ${name}?`,
      body: 'It goes back to the backlog on Today. A removed company is never proposed again on its own.',
      action: 'Bring back',
    };
  }
  if (row.target.kind === 'person') {
    return {
      title: `Take ${name} off the no-go list?`,
      body: 'Taking them off locks your choice: they can be emailed again.',
      action: 'Take it off',
    };
  }
  return {
    title: `Take ${name} off the no-go list?`,
    body: 'Taking it off locks your choice: the rules will not add it back for this posting, and a new posting that says the same raises a review item instead.',
    action: 'Take it off',
  };
}

/** Mounted only while open, so every open starts with an empty reason. */
function LiftForm({ row, onCancel, onSubmit }: { row: CompanyEntry; onCancel: () => void; onSubmit: (reason: string) => void }) {
  const [why, setWhy] = useState('');
  // The dialog stays mounted through its exit animation, so a double click or a held Enter must not submit twice.
  const submitted = useRef(false);
  const reason = why.trim();
  const ready = reason.length > 0;
  const copy = copyFor(row);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!ready || submitted.current) return;
    submitted.current = true;
    onSubmit(reason);
  };
  return (
    <form onSubmit={submit} className="grid gap-4">
      <DialogHeader>
        <DialogTitle>{copy.title}</DialogTitle>
        <DialogDescription>{copy.body}</DialogDescription>
      </DialogHeader>
      <div className="grid gap-1.5">
        <Label htmlFor="no-go-why">Why</Label>
        <Input
          id="no-go-why"
          placeholder="For example: their recruiter said they now sponsor"
          value={why}
          onChange={(e) => setWhy(e.target.value)}
        />
      </div>
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        {/* aria-disabled, not disabled: the button stays focusable and says why through its state. */}
        <Button type="submit" aria-disabled={ready ? undefined : true} className="aria-disabled:cursor-not-allowed aria-disabled:opacity-50">
          {copy.action}
        </Button>
      </DialogFooter>
    </form>
  );
}

/**
 * Take a no-go entry off the list, or bring back a removed company. A reason is required; the dialog
 * only reports it, and the page commits the write at once.
 */
export function LiftDialog({
  row,
  open,
  onOpenChange,
  onConfirm,
}: {
  /** Kept after closing, so the dialog keeps its words through the exit animation. */
  row: CompanyEntry | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (row: CompanyEntry, reason: string) => void;
}) {
  return (
    <Dialog open={open && row !== null} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={false}>
        {row ? (
          <LiftForm
            row={row}
            onCancel={() => onOpenChange(false)}
            onSubmit={(reason) => {
              onConfirm(row, reason);
              onOpenChange(false);
            }}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
