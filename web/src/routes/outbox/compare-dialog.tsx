import { Fragment } from 'react';
import { usePageClient, type DraftCompare, type Id } from '@/api';
import { QueryState } from '@/components/state-views';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { useViewQuery } from '@/pending/pending';

/** The text with the shared stretches marked. Ranges are clamped, sorted and merged first. */
function Marked({ text, ranges }: { text: string; ranges: { start: number; end: number }[] }) {
  const merged: { start: number; end: number }[] = [];
  for (const r of ranges
    .map((x) => ({ start: Math.max(0, x.start), end: Math.min(text.length, x.end) }))
    .filter((x) => x.end > x.start)
    .sort((a, b) => a.start - b.start)) {
    const last = merged[merged.length - 1];
    if (last && r.start <= last.end) last.end = Math.max(last.end, r.end);
    else merged.push({ ...r });
  }
  let at = 0;
  return (
    <>
      {merged.map((r) => {
        const before = text.slice(at, r.start);
        at = r.end;
        return (
          <Fragment key={r.start}>
            {before}
            <mark className="rounded-sm bg-warn-bd/60 text-foreground">{text.slice(r.start, r.end)}</mark>
          </Fragment>
        );
      })}
      {text.slice(at)}
    </>
  );
}

function Side({ title, body, ranges }: { title: string; body: string; ranges: { start: number; end: number }[] }) {
  return (
    <section className="grid min-w-0 content-start gap-2">
      <h3 className="text-sm font-medium">{title}</h3>
      <p className="max-h-[50vh] overflow-y-auto rounded-md border bg-subtle p-3 text-sm leading-[22px] break-words whitespace-pre-wrap">
        <Marked text={body} ranges={ranges} />
      </p>
    </section>
  );
}

function Compared({ c }: { c: DraftCompare }) {
  return (
    <div className="grid gap-3">
      <p className="text-sm text-muted-foreground">
        <b className="font-medium text-foreground">{`${Math.round(c.similarity * 100)}% the same`}</b>. Shared stretches are marked.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Side title={`${c.a.personName}'s draft`} body={c.a.body} ranges={c.matches.map((m) => ({ start: m.aStart, end: m.aEnd }))} />
        <Side title={`${c.b.personName}'s draft`} body={c.b.body} ranges={c.matches.map((m) => ({ start: m.bStart, end: m.bEnd }))} />
      </div>
    </div>
  );
}

/** Mounted only while open, so the comparison is fetched when he asks for it. */
function CompareBody({ messageId, otherMessageId }: { messageId: Id; otherMessageId: Id }) {
  const client = usePageClient('outbox');
  const query = useViewQuery(['compare', messageId, otherMessageId], () => client.compareDrafts(messageId, otherMessageId));
  return (
    <QueryState query={query} skeleton={<Skeleton className="h-48 w-full" />} empty={null}>
      {(c) => <Compared c={c} />}
    </QueryState>
  );
}

/** Spec 5.3, "Compare drafts": two drafts side by side, the shared stretches marked. Read only. */
export function CompareDialog({
  messageId,
  otherMessageId,
  open,
  onOpenChange,
}: {
  messageId: Id;
  otherMessageId: Id;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>Compare drafts</DialogTitle>
          <DialogDescription>Change the middle paragraph or the ask in yours, then approve.</DialogDescription>
        </DialogHeader>
        <CompareBody messageId={messageId} otherMessageId={otherMessageId} />
      </DialogContent>
    </Dialog>
  );
}
