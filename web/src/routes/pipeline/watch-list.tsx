import { useId } from 'react';
import { Link } from 'react-router';
import { ExternalLink } from 'lucide-react';
import { pollMs, queryKeys, usePageClient, type WatchItem, type WatchView } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { safeHref } from '@/lib/safe-href';
import { watchKindLabel } from '@/lib/status';
import { usePendingWrite, useViewQuery } from '@/pending/pending';
import { quietLink } from '@/lib/quiet-link';
import { formatMonthDay } from '@/lib/short-dates';

/**
 * The one link under an item: the page that changed (an outside address, opened in a new tab and never fetched), the
 * company's own page for a new reason to write, or the Needs you view for a possible duplicate. An address that is
 * not http or https shows its words as plain text.
 */
function ItemLink({ item }: { item: WatchItem }) {
  const muted = 'text-xs text-muted-foreground';
  if (item.kind === 'merge_candidate') {
    return (
      <Link to="/needs-you" className={`${quietLink} ${muted}`}>
        Decide in Needs you
      </Link>
    );
  }
  if (item.kind === 'new_reason_no_role') {
    return (
      <Link to={`/companies/${item.company.id}`} className={`${quietLink} ${muted}`}>
        {`Open ${item.company.name}'s page`}
      </Link>
    );
  }
  if (!item.link) return null;
  const href = safeHref(item.link.url);
  if (!href) return <span className={muted}>{item.link.label}</span>;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className={`${quietLink} inline-flex items-center gap-1 ${muted}`}>
      {item.link.label}
      <ExternalLink aria-hidden className="size-3" />
    </a>
  );
}

/**
 * The watch list (design 16.1): careers pages that changed, new companies to reach and possible duplicates, each with
 * its reason as the API words it, its date and one link. It is the last tab of the Pipeline page, with its count in plain
 * grey (a long list is not an alarm). Dismiss is a pending write with Undo; the last one takes the section away. Absent
 * with no items.
 */
export function WatchList() {
  const client = usePageClient('plan');
  const write = usePendingWrite();
  const watch = useViewQuery(queryKeys.watch, () => client.watchList(), { pollMs: pollMs.default });
  const headingId = useId();
  const items = watch.data?.items ?? [];
  if (items.length === 0) return null;

  const dismiss = (item: WatchItem) => {
    const label = `Dismissed: ${item.company.name}.`;
    write({
      id: `watch:${item.id}`,
      label,
      afterCommit: label,
      patches: [
        {
          key: queryKeys.watch,
          apply: (v: WatchView) => ({ ...v, items: v.items.filter((i) => i.id !== item.id) }),
        },
      ],
      commit: (o) => client.dismissWatch(item.id, o),
    });
  };

  return (
    <section aria-labelledby={headingId}>
      <h3 id={headingId} className="sr-only">
        Watch list
        <span data-testid="count">{items.length}</span>
      </h3>
      <ul className="divide-y overflow-hidden rounded-xl border bg-card">
        {items.map((item) => (
          <li key={item.id} className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-3 px-4 py-3 transition-colors duration-150 hover:bg-muted/40">
            <div className="grid gap-1">
              <p className="flex flex-wrap items-center gap-2">
                <Badge variant="idle">{watchKindLabel[item.kind]}</Badge>
                <Link to={`/companies/${item.company.id}`} className="rounded-sm text-sm font-medium outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/70">
                  {item.company.name}
                </Link>
                <span className="text-xs text-muted-foreground">{formatMonthDay(item.at)}</span>
              </p>
              <p className="max-w-[62ch] text-sm">{item.text}</p>
              <ItemLink item={item} />
            </div>
            <Button type="button" variant="ghost" size="sm" onClick={() => dismiss(item)}>
              Dismiss
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}
