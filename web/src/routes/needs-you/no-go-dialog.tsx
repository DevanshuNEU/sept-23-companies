import { useRef, useState, type FormEvent } from 'react';
import type { NoGoReason } from '@/api';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';

// The four reasons of the needs-you mockup, in its order.
const REASONS: { value: NoGoReason; label: string }[] = [
  { value: 'no_sponsorship', label: 'Does not sponsor' },
  { value: 'citizenship', label: 'Citizenship or clearance required' },
  { value: 'over_300', label: 'Over 300 people' },
  { value: 'other', label: 'Other' },
];

export interface NoGoStart {
  reason: NoGoReason;
  source: string;
}

/** Mounted only while open, so every open starts from `start` (Does not sponsor and an empty source by default). */
function NoGoForm({
  name,
  start,
  onCancel,
  onSubmit,
}: {
  name: string;
  start: NoGoStart;
  onCancel: () => void;
  onSubmit: (reason: NoGoReason, source: string) => void;
}) {
  const [reason, setReason] = useState<NoGoReason>(start.reason);
  const [source, setSource] = useState(start.source);
  // The dialog stays mounted through its exit animation, so a double click or a held Enter must not write twice.
  const submitted = useRef(false);
  const ready = source.trim() !== '';
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!ready || submitted.current) return;
    submitted.current = true;
    onSubmit(reason, source.trim());
  };
  return (
    <form onSubmit={submit} className="grid gap-4">
      <AlertDialogHeader>
        <AlertDialogTitle>{`Add ${name} to the no-go list?`}</AlertDialogTitle>
        <AlertDialogDescription>
          It leaves Today and the backlog, and every queued email there stops. You can take it off the no-go list later.
        </AlertDialogDescription>
      </AlertDialogHeader>
      <RadioGroup value={reason} onValueChange={(v) => setReason(v as NoGoReason)} aria-label="Reason" className="gap-2.5">
        {REASONS.map((r) => (
          <div key={r.value} className="flex items-center gap-2.5">
            <RadioGroupItem id={`nogo-reason-${r.value}`} value={r.value} />
            <Label htmlFor={`nogo-reason-${r.value}`} className="font-normal">
              {r.label}
            </Label>
          </div>
        ))}
      </RadioGroup>
      <Input
        placeholder="Source: a link or where you saw it"
        aria-label="Source"
        aria-required
        value={source}
        onChange={(e) => setSource(e.target.value)}
      />
      <AlertDialogFooter>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" variant="destructive" aria-disabled={!ready} className="aria-disabled:opacity-50">
          Mark no-go
        </Button>
      </AlertDialogFooter>
    </form>
  );
}

/** Mark a company no-go with a reason and a required source. The caller commits the write. */
export function NoGoDialog({
  name,
  start,
  open,
  onOpenChange,
  onMark,
}: {
  name: string;
  start: NoGoStart;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onMark: (reason: NoGoReason, source: string) => void;
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <NoGoForm
          name={name}
          start={start}
          onCancel={() => onOpenChange(false)}
          onSubmit={(reason, source) => {
            onMark(reason, source);
            onOpenChange(false);
          }}
        />
      </AlertDialogContent>
    </AlertDialog>
  );
}
