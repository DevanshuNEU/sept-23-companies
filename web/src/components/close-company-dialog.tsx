import { useRef, useState, type FormEvent } from 'react';
import type { CloseReason, CompanyRef } from '@/api';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';

// The seven reasons of design section 7, in its order.
const REASONS: { value: CloseReason; label: string; hint?: string }[] = [
  { value: 'referral', label: 'Referral' },
  { value: 'interview', label: 'Interview' },
  { value: 'no_sponsorship', label: 'No sponsorship', hint: '(adds it to the no-go list)' },
  { value: 'no_referrals', label: 'No referrals' },
  { value: 'already_hired', label: 'Already hired' },
  { value: 'not_now', label: 'Not now' },
  { value: 'other', label: 'Other' },
];

/** Mounted only while the dialog is open, so every open starts with Interview and an empty note, as in the mockup. */
function CloseForm({
  company,
  onCancel,
  onSubmit,
}: {
  company: CompanyRef;
  onCancel: () => void;
  onSubmit: (reason: CloseReason, note: string) => void;
}) {
  const [reason, setReason] = useState<CloseReason>('interview');
  const [note, setNote] = useState('');
  // The dialog stays mounted through its exit animation, so a double click or a held Enter must not close twice.
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
        <DialogTitle>{`Close ${company.name}`}</DialogTitle>
        <DialogDescription>
          Every queued email here stops for good, follow-ups included. Referral and interview move it to Done.
        </DialogDescription>
      </DialogHeader>
      <RadioGroup value={reason} onValueChange={(v) => setReason(v as CloseReason)} aria-label="Reason" className="gap-2.5">
        {REASONS.map((r) => (
          <div key={r.value} className="flex items-center gap-2.5">
            <RadioGroupItem id={`close-reason-${r.value}`} value={r.value} />
            <Label htmlFor={`close-reason-${r.value}`} className="font-normal">
              {r.hint ? `${r.label} ` : r.label}
              {r.hint ? <span className="text-xs text-muted-foreground">{r.hint}</span> : null}
            </Label>
          </div>
        ))}
      </RadioGroup>
      <Input placeholder="Note (optional)" aria-label="Note" value={note} onChange={(e) => setNote(e.target.value)} />
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit">Close company</Button>
      </DialogFooter>
    </form>
  );
}

/** Close a company with a reason (design 5.3 and section 7). Confirming calls onClose once and commits at once: this dialog has no undo and the caller adds none. */
export function CloseCompanyDialog({
  company,
  open,
  onOpenChange,
  onClose,
}: {
  company: CompanyRef;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onClose: (reason: CloseReason, note: string) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={false}>
        <CloseForm
          company={company}
          onCancel={() => onOpenChange(false)}
          onSubmit={(reason, note) => {
            onClose(reason, note);
            onOpenChange(false);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}
