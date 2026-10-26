import { useLayoutEffect, useRef, useState, type CSSProperties, type Ref } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { ChevronDown, Inbox, Reply, Search } from 'lucide-react';
import type { Id, IsoTime, OutboxCounts, OutboxGroup, OutboxItem, OutboxView } from '@/api';
import { EmptyState } from '@/components/state-views';
import { StatusBadge } from '@/components/status-badge';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { useAnnounce, useHotkeys, useLiveSelection } from '@/keyboard/hotkeys';
import { ageDays, formatDay, formatLocalTime } from '@/lib/format';
import { useStableOrder } from '@/lib/stable-order';
import { messageStatus, touchStatus, type Status } from '@/lib/status';
import { cn } from '@/lib/utils';

/** Spec 5.3, "List groups, in order". */
const GROUPS: { group: OutboxGroup; label: string }[] = [
  { group: 'replied', label: 'Replied' },
  { group: 'review', label: 'Drafts to review' },
  { group: 'approved', label: 'Approved' },
  { group: 'sending', label: 'Sending' },
  { group: 'checking', label: 'Checking whether it went out' },
  { group: 'sent', label: 'Sent' },
  { group: 'paused', label: 'Paused' },
  { group: 'closed', label: 'Closed' },
];

/** Past this many rows the Sent group renders only what is on screen (spec section 6). */
const VIRTUAL_AFTER = 200;
const ROW_PX = 75;

/** The badge on a row: the stored state in one word, and for a draft its worst check. */
// eslint-disable-next-line react-refresh/only-export-components
export function rowStatus(item: OutboxItem): Status {
  if (item.touchState === 'replied' || item.touchState === 'bounced') return touchStatus[item.touchState];
  if (item.state === 'skipped') return messageStatus.skipped;
  if (item.touchState === 'closed') return touchStatus.closed;
  if (item.group === 'paused' && (item.state === 'drafted' || item.state === 'approved')) {
    return { label: 'Paused', tone: 'idle', icon: 'pause' };
  }
  if (item.state !== 'drafted' && item.state !== 'planned') return messageStatus[item.state];
  if (item.blockCount > 0) return { label: 'Blocked', tone: 'bad' };
  if (item.warnCount > 0) return { label: item.warnCount === 1 ? '1 warning' : `${item.warnCount} warnings`, tone: 'warn' };
  if (item.seq > 0) return { label: `Follow-up ${item.seq} of 3`, tone: 'idle' };
  return messageStatus.drafted;
}

/** The right of the first line: when an approved email sends ("after 11:05 ET"), else the row's time or day. */
function when(item: OutboxItem, asOf: IsoTime): string {
  if (item.state === 'approved' && item.schedule) return item.schedule.text.replace(/^Sends /, '');
  return ageDays(item.at, asOf) === 0 ? formatLocalTime(item.at) : formatDay(item.at).replace(/^\S+ /, '');
}

type Extra = { asOf: IsoTime; counts: OutboxCounts; closedBreakdown: OutboxView['closedBreakdown']; sentToday: number };

/** The muted note at the right of a group head. */
function groupNote(group: OutboxGroup, rows: OutboxItem[], x: Extra): string | null {
  switch (group) {
    case 'replied':
      return 'holds the company';
    case 'review': {
      const blocked = rows.filter((i) => i.state === 'drafted' && i.blockCount > 0).length;
      return blocked > 0 ? `${blocked} blocked` : null;
    }
    case 'approved':
      return 'waiting for a slot';
    case 'sent':
      return `${x.sentToday} today`;
    case 'paused': {
      const names = [...new Set(rows.map((i) => i.company.name))];
      return names.length > 0 ? `${names.join(', ')}, by you` : null;
    }
    case 'closed': {
      const b = x.closedBreakdown;
      return `bounced ${b.bounced}, skipped ${b.skipped}, company closed ${b.companyClosed}`;
    }
    default:
      return null;
  }
}

/**
 * The group a selected row shows in. A refresh can move it (approved, sent); while it stays selected it
 * keeps the group it had when it was selected, so nothing moves under the cursor (spec section 6).
 */
function useHeldGroup(items: OutboxItem[], selected: Id | null): (i: OutboxItem) => OutboxGroup {
  const [held, setHeld] = useState<{ id: Id; group: OutboxGroup } | null>(null);
  const current = selected === null ? undefined : items.find((i) => i.touchId === selected);
  let next = held;
  if (!current) next = null;
  else if (held?.id !== current.touchId) next = { id: current.touchId, group: current.group };
  // Adjusting state while rendering: React re-runs this render at once and the second pass changes nothing.
  if (next !== held) setHeld(next);
  return (i) => (next !== null && i.touchId === next.id ? next.group : i.group);
}

const bySortKey = (a: OutboxItem, b: OutboxItem) => (a.sortKey < b.sortKey ? -1 : a.sortKey > b.sortKey ? 1 : 0);

function Row({
  item,
  asOf,
  active,
  onSelect,
  rowRef,
  style,
  index,
}: {
  item: OutboxItem;
  asOf: IsoTime;
  active: boolean;
  onSelect: (id: Id) => void;
  rowRef: Ref<HTMLDivElement>;
  style?: CSSProperties;
  index?: number;
}) {
  const replied = item.touchState === 'replied';
  return (
    <div
      role="option"
      aria-selected={active}
      data-active={active}
      data-touch={item.touchId}
      data-index={index}
      tabIndex={-1}
      ref={rowRef}
      style={style}
      onClick={() => onSelect(item.touchId)}
      className={cn(
        'grid cursor-default gap-[3px] border-b py-2.5 pr-3 pl-3.5 outline-none hover:bg-muted/55',
        active && 'bg-muted shadow-[inset_2px_0_0_var(--foreground)] hover:bg-muted',
        replied && (active ? 'shadow-[inset_3px_0_0_var(--ok-dot)]' : 'shadow-[inset_2px_0_0_var(--ok-dot)]'),
      )}
    >
      <div className="flex min-w-0 items-baseline gap-1.5">
        <b className="font-medium whitespace-nowrap">{item.person.name}</b>
        <span className="truncate text-[13px] text-muted-foreground">{item.company.name}</span>
        <span className="ml-auto text-xs whitespace-nowrap text-muted-foreground tabular-nums">{when(item, asOf)}</span>
      </div>
      <div className="truncate text-[13px] text-muted-foreground">{item.preview}</div>
      <div className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
        <StatusBadge status={rowStatus(item)} dot />
        <span className="truncate">{item.summary}</span>
      </div>
    </div>
  );
}

export function OutboxList({
  items,
  selected,
  onSelect,
  ...extra
}: { items: OutboxItem[]; selected: Id | null; onSelect: (touchId: Id) => void } & Extra) {
  const say = useAnnounce();
  const [query, setQuery] = useState('');
  const [collapsed, setCollapsed] = useState<ReadonlySet<OutboxGroup>>(() => new Set(['closed']));
  // Rows he has seen. A refetch that brings new ones holds them behind an "N new" pill until he asks.
  const [seen, setSeen] = useState<ReadonlySet<Id>>(() => new Set(items.map((i) => i.touchId)));
  const scrollRef = useRef<HTMLDivElement>(null);
  const sentRef = useRef<HTMLDivElement>(null);
  const rowEls = useRef(new Map<Id, HTMLElement>());

  const ordered = useStableOrder([...items].sort(bySortKey), selected, (i) => i.touchId, (i) => i.group);
  const groupOf = useHeldGroup(items, selected);
  const isFresh = (i: OutboxItem) => !seen.has(i.touchId) && i.touchId !== selected;
  const q = query.trim().toLowerCase();
  const matches = (i: OutboxItem) => q === '' || [i.person.name, i.company.name, i.subject].some((s) => s.toLowerCase().includes(q));

  const buckets = GROUPS.map(({ group, label }) => {
    const all = ordered.filter((i) => groupOf(i) === group && matches(i));
    return { group, label, rows: all.filter((i) => !isFresh(i)), fresh: all.filter(isFresh).length };
  });
  const open = (group: OutboxGroup) => !collapsed.has(group);
  const visible = buckets.flatMap((b) => (open(b.group) ? b.rows : []));
  const anyFresh = buckets.some((b) => b.fresh > 0);

  const sentRows = buckets.find((b) => b.group === 'sent')?.rows ?? [];
  const virtual = sentRows.length > VIRTUAL_AFTER && open('sent');
  const [sentTop, setSentTop] = useState(0);
  // TanStack Virtual hands back functions React Compiler cannot memoize; this component does not need it to.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: virtual ? sentRows.length : 0,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_PX,
    overscan: 10,
    scrollMargin: sentTop,
    getItemKey: (index) => sentRows[index]?.touchId ?? index,
  });
  // Where the Sent rows start in the scroller moves with every row and pill above them.
  const above = buckets
    .slice(0, GROUPS.findIndex((g) => g.group === 'sent'))
    .map((b) => `${b.group}:${open(b.group) ? b.rows.length : 0}:${b.fresh}`)
    .join();
  useLayoutEffect(() => {
    if (virtual) setSentTop(sentRef.current?.offsetTop ?? 0);
  }, [virtual, above]);

  const reveal = () => setSeen(new Set(items.map((i) => i.touchId)));

  // The selection lives in the URL, which renders late: j and k step from the row he moved to last.
  const live = useLiveSelection<Id>();

  const go = (id: Id) => {
    live.move(id);
    onSelect(id);
    const item = items.find((i) => i.touchId === id);
    if (item) say(`${item.person.name}, ${item.company.name}, ${rowStatus(item).label.toLowerCase()}`);
    const sentAt = virtual ? sentRows.findIndex((i) => i.touchId === id) : -1;
    if (sentAt >= 0) virtualizer.scrollToIndex(sentAt, { align: 'auto' });
    else rowEls.current.get(id)?.scrollIntoView({ block: 'nearest' });
    // Selection and focus move together (spec section 12), so a screen reader follows the row.
    rowEls.current.get(id)?.focus({ preventScroll: true });
  };

  const move = (step: 1 | -1) => {
    const order = visible.map((i) => i.touchId);
    if (order.length === 0) return;
    const from = live.latest();
    const at = from === null ? -1 : order.indexOf(from);
    const id = at === -1 ? order[0] : order[Math.min(Math.max(at + step, 0), order.length - 1)];
    if (id !== undefined && id !== from) go(id);
  };

  const moveGroup = (step: 1 | -1) => {
    if (step === 1 && anyFresh) {
      reveal();
      return;
    }
    const groups = buckets.filter((b) => open(b.group) && b.rows.length > 0);
    if (groups.length === 0) return;
    const from = live.latest();
    const at = from === null ? -1 : groups.findIndex((b) => b.rows.some((i) => i.touchId === from));
    const target = groups[Math.min(Math.max(at + step, 0), groups.length - 1)];
    const id = target?.rows[0]?.touchId;
    if (id !== undefined && id !== from) go(id);
  };

  useHotkeys([
    { keys: 'j', label: 'Next row', group: 'Everywhere', run: () => move(1) },
    { keys: 'arrowdown', label: 'Next row', group: 'Everywhere', run: () => move(1) },
    { keys: 'k', label: 'Previous row', group: 'Everywhere', run: () => move(-1) },
    { keys: 'arrowup', label: 'Previous row', group: 'Everywhere', run: () => move(-1) },
    { keys: 'shift+j', label: 'Next group (shows new rows first)', group: 'Outbox', run: () => moveGroup(1) },
    { keys: 'shift+k', label: 'Previous group', group: 'Outbox', run: () => moveGroup(-1) },
  ]);

  const toggle = (group: OutboxGroup) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
    });

  const rowRef = (id: Id, measure?: (el: HTMLElement | null) => void) => (el: HTMLDivElement | null) => {
    if (el) rowEls.current.set(id, el);
    else rowEls.current.delete(id);
    measure?.(el);
  };

  const row = (item: OutboxItem, opts: { style?: CSSProperties; index?: number; measure?: (el: HTMLElement | null) => void } = {}) => (
    <Row
      key={item.touchId}
      item={item}
      asOf={extra.asOf}
      active={item.touchId === selected}
      onSelect={go}
      rowRef={rowRef(item.touchId, opts.measure)}
      style={opts.style}
      index={opts.index}
    />
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-none items-center gap-2 border-b px-3 py-2.5">
        <div className="relative flex-1">
          <Search aria-hidden className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by person, company or subject"
            aria-label="Filter"
            className="pl-8"
          />
        </div>
      </div>
      <div ref={scrollRef} className="relative min-h-0 flex-1 overflow-y-auto">
        {items.length === 0 ? (
          <EmptyState icon={Inbox} title="No drafts to review.">
            Paste people for a picked company in the side terminal. Claude&apos;s drafts show up here.
          </EmptyState>
        ) : (
          buckets.map((b) => {
            const count = extra.counts[b.group];
            if (count === 0 && b.rows.length === 0 && b.fresh === 0) return null;
            const note = groupNote(b.group, b.rows, extra);
            const isOpen = open(b.group);
            return (
              <section key={b.group} data-group={b.group} aria-label={b.label}>
                <button
                  type="button"
                  data-group-head={b.group}
                  aria-expanded={isOpen}
                  onClick={() => toggle(b.group)}
                  className="flex h-8 w-full items-center gap-1.5 border-b bg-subtle px-3 text-left text-xs font-medium text-muted-foreground outline-none focus-visible:ring-[3px] focus-visible:ring-ring/70"
                >
                  <ChevronDown aria-hidden className={cn('size-3.5 transition-transform', !isOpen && '-rotate-90')} />
                  {b.group === 'replied' ? <Reply aria-hidden className="size-3.5 text-ok" /> : null}
                  <span data-label>{b.label}</span>
                  <span className="tabular-nums">{count}</span>
                  {note ? <span className="ml-auto truncate font-normal">{note}</span> : null}
                </button>
                {isOpen && b.fresh > 0 ? (
                  <div className="flex justify-center border-b py-1.5">
                    <button
                      type="button"
                      onClick={reveal}
                      className="rounded-full border border-info-bd bg-info-bg px-2.5 py-0.5 text-xs font-medium text-info outline-none focus-visible:ring-[3px] focus-visible:ring-ring/70"
                    >
                      {`${b.fresh} new`}
                    </button>
                  </div>
                ) : null}
                {isOpen ? (
                  <div role="listbox" aria-label={b.label}>
                    {b.group === 'sent' && virtual ? (
                      <div ref={sentRef} className="relative" style={{ height: virtualizer.getTotalSize() }}>
                        {virtualizer.getVirtualItems().map((v) => {
                          const item = sentRows[v.index];
                          if (!item) return null;
                          return row(item, {
                            index: v.index,
                            measure: virtualizer.measureElement,
                            style: { position: 'absolute', top: 0, left: 0, width: '100%', transform: `translateY(${v.start - sentTop}px)` },
                          });
                        })}
                      </div>
                    ) : (
                      b.rows.map((item) => row(item))
                    )}
                  </div>
                ) : null}
              </section>
            );
          })
        )}
      </div>
    </div>
  );
}

/** Loading: list rows as blocks at their real height. */
export function OutboxListSkeleton() {
  return (
    <div aria-hidden>
      {Array.from({ length: 8 }, (_, i) => (
        <div key={i} className="grid gap-2 border-b py-2.5 pr-3 pl-3.5" style={{ height: ROW_PX }}>
          <Skeleton className="h-3.5 w-40" />
          <Skeleton className="h-3 w-56" />
          <Skeleton className="h-3 w-32" />
        </div>
      ))}
    </div>
  );
}
