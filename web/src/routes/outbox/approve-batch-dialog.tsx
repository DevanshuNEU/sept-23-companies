import { useState } from 'react';
import type { Id, OutboxItem } from '@/api';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { FOLLOWUP_COUNT } from '@/lib/followups';
import { approveTitle, batchSummary } from './approve-batch';

const kindOf = (d: OutboxItem) => (d.seq === 0 ? 'First email' : `Follow-up ${d.seq} of ${FOLLOWUP_COUNT}`);

function Body({ drafts, onClose, onConfirm }: { drafts: OutboxItem[]; onClose: () => void; onConfirm: (picked: OutboxItem[]) => void }) {
  const [ticked, setTicked] = useState<ReadonlySet<Id>>(() => new Set(drafts.map((d) => d.messageId)));
  const picked = drafts.filter((d) => ticked.has(d.messageId));
  const all = picked.length === drafts.length;
  const toggle = (id: Id) =>
    setTicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <DialogContent showCloseButton={false} className="sm:max-w-xl">
      <DialogHeader>
        <DialogTitle>{approveTitle(picked.length)}</DialogTitle>
        <DialogDescription>{picked.length > 0 ? batchSummary(picked) : 'Tick the drafts to approve.'}</DialogDescription>
      </DialogHeader>
      <div>
        <label className="flex items-center gap-2.5 border-b pb-2 text-sm font-medium">
          <Checkbox
            aria-label="Select all"
            checked={all ? true : picked.length === 0 ? false : 'indeterminate'}
            onCheckedChange={() => setTicked(all ? new Set() : new Set(drafts.map((d) => d.messageId)))}
          />
          <span aria-hidden>Select all</span>
          <span className="ml-auto text-xs font-normal text-muted-foreground tabular-nums">{`${picked.length} of ${drafts.length}`}</span>
        </label>
        <div className="max-h-[60vh] overflow-y-auto">
          {drafts.map((d) => (
            <label key={d.messageId} className="flex cursor-pointer items-start gap-2.5 border-b py-2.5 text-sm hover:bg-muted/55">
              <Checkbox className="mt-0.5" checked={ticked.has(d.messageId)} onCheckedChange={() => toggle(d.messageId)} />
              <span className="grid min-w-0 flex-1 gap-0.5">
                <span className="flex items-baseline gap-1.5">
                  <span className="font-medium whitespace-nowrap">{d.person.name}</span>
                  <span className="truncate text-[13px] text-muted-foreground">{d.company.name}</span>
                  <span className="ml-auto text-xs whitespace-nowrap text-muted-foreground">{kindOf(d)}</span>
                </span>
                <span className="truncate text-[13px] text-muted-foreground">{d.subject}</span>
              </span>
            </label>
          ))}
        </div>
      </div>
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="button" disabled={picked.length === 0} onClick={() => onConfirm(picked)}>
          {picked.length === 0 ? 'Approve' : `Approve ${picked.length}`}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

/**
 * The list of clean drafts the Approve bar opens. It is open while `drafts` is set. It never approves anything
 * itself: its confirm hands the ticked drafts to the caller, which queues the undoable batch.
 */
export function ApproveBatchDialog({
  drafts,
  onClose,
  onConfirm,
}: {
  drafts: OutboxItem[] | null;
  onClose: () => void;
  onConfirm: (picked: OutboxItem[]) => void;
}) {
  return (
    <Dialog open={drafts !== null} onOpenChange={(open) => (open ? undefined : onClose())}>
      {drafts ? <Body drafts={drafts} onClose={onClose} onConfirm={onConfirm} /> : null}
    </Dialog>
  );
}
