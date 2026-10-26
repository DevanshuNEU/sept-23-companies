import type { ReplyKind } from '@/api';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { replyKindStatus } from '@/lib/status';

export type LoggedReply = Exclude<ReplyKind, 'unsorted' | 'auto_reply'>;

// The four reply kinds, as for email. Said no and Asked to stop add a no-go, so their callers commit them at once.
const REPLY_KINDS: LoggedReply[] = ['interested', 'not_now', 'said_no', 'asked_to_stop'];

/**
 * "What kind of reply?" for a reply logged on LinkedIn or answered on Today. `from` says who and where ("Lena Ortiz
 * on LinkedIn"); the dialog is open while it is set, and `onClose` runs for Cancel, Esc and a click outside.
 */
export function ReplyKindDialog({
  from,
  onPick,
  onClose,
}: {
  from: string | null;
  onPick: (kind: LoggedReply) => void;
  onClose: () => void;
}) {
  return (
    <Dialog open={from !== null} onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent showCloseButton={false} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>What kind of reply?</DialogTitle>
          <DialogDescription>
            {from ? `From ${from}. Said no and Asked to stop add them to the no-go list.` : null}
          </DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-2">
          {REPLY_KINDS.map((kind) => (
            <Button key={kind} type="button" variant="outline" onClick={() => onPick(kind)}>
              {replyKindStatus[kind].label}
            </Button>
          ))}
        </div>
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
