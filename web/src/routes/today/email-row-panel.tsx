import { Link } from 'react-router';
import { Inbox } from 'lucide-react';
import { pollMs, queryKeys, usePageClient, type IsoTime, type PlanRow } from '@/api';
import { StatusBadge } from '@/components/status-badge';
import { Button } from '@/components/ui/button';
import { messageStatus } from '@/lib/status';
import { useViewQuery } from '@/pending/pending';
import { MAIN, CarriedOver, CompanyLink, PanelHead, PanelReason, QuietActions, SkippedLine, type PanelActions } from './plan-panel';

/**
 * An email row (design 16.1): the company's draft in the Outbox with its status, and Review in Outbox as the one
 * next step. The sender and the reply reader log what happens to email, so no logging key acts here.
 */
export function EmailRowPanel({
  row,
  asOf,
  actions,
  headingId,
}: {
  row: PlanRow;
  asOf: IsoTime;
  actions: Pick<PanelActions, 'onCarry' | 'onNotNow'>;
  headingId?: string;
}) {
  const client = usePageClient('plan');
  const outbox = useViewQuery(queryKeys.outbox, () => client.outbox(), { pollMs: pollMs.outbox });
  const item = row.emailTouchId === null ? undefined : outbox.data?.items.find((i) => i.touchId === row.emailTouchId);
  const person = row.person?.person ?? item?.person ?? null;
  const todo = row.state === 'todo';
  const href = row.emailTouchId === null ? '/outbox' : `/outbox/${row.emailTouchId}`;

  return (
    <div className="grid max-w-[720px] gap-4">
      <PanelHead
        row={row}
        title={`${row.company.name}, by email`}
        headingId={headingId}
        meta={
          <>
            <span>
              {person ? `${person.name} at ` : 'At '}
              <CompanyLink row={row} />
              {', by email'}
            </span>
            <CarriedOver row={row} asOf={asOf} />
          </>
        }
      />
      <PanelReason row={row} />

      <section aria-label="In the Outbox" className="rounded-lg border bg-subtle">
        <div className="flex items-center gap-2 border-b px-4 py-2.5 text-[13px]">
          <h3 className="font-medium">In the Outbox</h3>
          <span className="ml-auto text-xs text-muted-foreground">{todo ? 'To review' : 'Reviewed'}</span>
        </div>
        {person ? (
          <div className="flex items-center gap-2 px-4 py-2.5 text-sm">
            <b className="font-medium">{person.name}</b>
            <span className="text-[13px] text-muted-foreground">{person.title}</span>
            <span className="ml-auto">{item ? <StatusBadge status={messageStatus[item.state]} dot /> : null}</span>
          </div>
        ) : null}
        <p className="border-t px-4 py-2.5 text-xs text-muted-foreground">
          The sender and the reply reader log what happens to email, so nothing here needs a key.
        </p>
      </section>

      <div className="flex flex-wrap items-center gap-2">
        <Button asChild variant={todo ? 'default' : 'outline'} size="sm" className={MAIN}>
          <Link to={href}>
            <Inbox aria-hidden />
            Review in Outbox
          </Link>
        </Button>
        <span className="flex-1" />
        {todo ? <QuietActions actions={actions} /> : null}
      </div>
      {row.state === 'skipped' ? <SkippedLine /> : null}
    </div>
  );
}
