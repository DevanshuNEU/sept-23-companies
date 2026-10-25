import { useRef, useState, type FormEvent } from 'react';
import type { RemoveReason } from '@/api';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';

// The four reasons of design 5.1, in its order.
const REASONS: { value: RemoveReason; label: string }[] = [
  { value: 'not_a_fit', label: 'Not a fit for me' },
  { value: 'already_in_touch', label: 'Already applied or in touch another way' },
  { value: 'bigger_than_it_looks', label: 'Bigger than it looks' },
  { value: 'other', label: 'Other' },
];

/** Mounted only while open, so every open starts on the first reason with an empty note, as in the mockup. */
function RemoveForm({
  name,
  onCancel,
  onSubmit,
}: {
  name: string;
  onCancel: () => void;
  onSubmit: (reason: RemoveReason, note: string) => void;
}) {
  const [reason, setReason] = useState<RemoveReason>('not_a_fit');
  const [note, setNote] = useState('');
  // The dialog stays mounted through its exit animation, so a double click or a held Enter must not remove twice.
  const submitted = useRef(false);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (submitted.current) return;
    submitted.current = true;
    onSubmit(reason, note.trim());
  };
  return (
    <form onSubmit={submit} className="grid gap-4">
      <DialogHeader>
        <DialogTitle>{`Remove ${name} for good?`}</DialogTitle>
        <DialogDescription>
          It will never be proposed again. It stays searchable and shows on the No-go list under Removed by you, where
          you can bring it back.
        </DialogDescription>
      </DialogHeader>
      <RadioGroup value={reason} onValueChange={(v) => setReason(v as RemoveReason)} aria-label="Reason" className="gap-2.5">
        {REASONS.map((r) => (
          <div key={r.value} className="flex items-center gap-2.5">
            <RadioGroupItem id={`remove-reason-${r.value}`} value={r.value} />
            <Label htmlFor={`remove-reason-${r.value}`} className="font-normal">
              {r.label}
            </Label>
          </div>
        ))}
      </RadioGroup>
      <Input placeholder="Note (optional)" aria-label="Note" value={note} onChange={(e) => setNote(e.target.value)} />
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit">Remove</Button>
      </DialogFooter>
    </form>
  );
}

/** Remove a company from Today for good, with a reason and an optional note. The caller commits the write. */
export function RemoveDialog({
  name,
  open,
  onOpenChange,
  onRemove,
}: {
  name: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRemove: (reason: RemoveReason, note: string) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={false}>
        <RemoveForm
          name={name}
          onCancel={() => onOpenChange(false)}
          onSubmit={(reason, note) => {
            onRemove(reason, note);
            onOpenChange(false);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}
