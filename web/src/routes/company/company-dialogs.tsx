import { useViewQuery } from '@/pending/pending';
import { queryKeys, useClient, type CompanyView, type Id, type MessageState } from '@/api';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { messageStatus } from '@/lib/status';

const emailWord = (seq: number) => (seq === 0 ? 'first email' : `follow-up ${seq}`);
const line = (name: string, seq: number, state: MessageState) => `${name}, ${emailWord(seq)} (${messageStatus[state].label})`;

function List({ query }: { query: { data: string[] | undefined; isError: boolean } }) {
  if (query.data === undefined) {
    return query.isError ? (
      <p className="text-sm text-muted-foreground">The queued emails did not load. You can still resume.</p>
    ) : (
      <Skeleton className="h-10 w-full" />
    );
  }
  if (query.data.length === 0) return <p className="text-sm text-muted-foreground">No emails are queued here.</p>;
  return (
    <ul aria-label="Emails that send again" className="grid gap-1 text-sm">
      {query.data.map((l) => (
        <li key={l}>{l}</li>
      ))}
    </ul>
  );
}

/** The emails a reply is holding: read from the reply's own detail. */
function HeldByReply({ touchId }: { touchId: Id }) {
  const client = useClient();
  const query = useViewQuery(queryKeys.outboxDetail(touchId), () => client.outboxDetail(touchId));
  return <List query={{ data: query.data?.held.map((h) => line(h.personName, h.seq, h.state)), isError: query.isError }} />;
}

/** The emails a pause is holding: the company's items in the Outbox's Paused group. */
function PausedItems({ companyId }: { companyId: Id }) {
  const client = useClient();
  const query = useViewQuery(queryKeys.outbox, () => client.outbox());
  const items = query.data?.items.filter((i) => i.company.id === companyId && i.group === 'paused');
  return <List query={{ data: items?.map((i) => line(i.person.name, i.seq, i.state)), isError: query.isError }} />;
}

/** Resume asks first and lists what will send again (design section 7). */
export function ResumeDialog({ view, open, onOpenChange, onResume }: { view: CompanyView; open: boolean; onOpenChange: (o: boolean) => void; onResume: () => void }) {
  const { name, id, heldByTouchId } = view.company;
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{`Resume ${name}`}</AlertDialogTitle>
          <AlertDialogDescription>These emails go back to the sender and follow their normal schedule.</AlertDialogDescription>
        </AlertDialogHeader>
        {heldByTouchId !== null ? <HeldByReply touchId={heldByTouchId} /> : <PausedItems companyId={id} />}
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={onResume}>Resume company</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/** Reopen asks first: the company comes back with its history (design 5.7). */
export function ReopenDialog({ name, open, onOpenChange, onReopen }: { name: string; open: boolean; onOpenChange: (o: boolean) => void; onReopen: () => void }) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{`Reopen ${name}`}</AlertDialogTitle>
          <AlertDialogDescription>It comes back to your list with its full history. Drafts stopped when it closed come back too.</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={onReopen}>Reopen company</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
