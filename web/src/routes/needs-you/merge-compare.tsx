import { EqualNot, TriangleAlert } from 'lucide-react';
import type { MergeSide, ReviewItem } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { companyStatus } from '@/lib/status';
import { cn } from '@/lib/utils';
import { ActionButton } from './action-button';

type Keep = 'a' | 'b';

/** "Mesh's", "Corvid Works'". */
// eslint-disable-next-line react-refresh/only-export-components
export const possessive = (name: string) => (name.endsWith('s') ? `${name}'` : `${name}'s`);

/** The head of a merge question: both names, the item's company first. */
// eslint-disable-next-line react-refresh/only-export-components
export const mergeName = (item: ReviewItem) => (item.merge ? `${item.merge.a.name} and ${item.merge.b.name}` : item.company.name);

const count = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** "Ashby, meshsystems" from the API's "ashby:meshsystems"; several boards are listed with semicolons. */
function boardsText(boards: string[]): string {
  if (boards.length === 0) return 'None';
  return boards
    .map((b) => {
      const at = b.indexOf(':');
      const ats = at === -1 ? b : b.slice(0, at);
      const token = at === -1 ? '' : b.slice(at + 1);
      const name = ats.charAt(0).toUpperCase() + ats.slice(1);
      return token ? `${name}, ${token}` : name;
    })
    .join('; ');
}

// The facts the mockup compares, in its order. Each reads one side as the text shown.
const FACTS: { label: string; read: (s: MergeSide) => string; num?: boolean }[] = [
  { label: 'Domain', read: (s) => s.domain ?? 'None' },
  { label: 'Size', read: (s) => s.size ?? 'Unknown' },
  { label: 'Stage', read: (s) => s.stage ?? 'Unknown' },
  { label: 'Boards', read: (s) => boardsText(s.boards) },
  { label: 'Open postings', read: (s) => String(s.openPostings), num: true },
  { label: 'People', read: (s) => String(s.people), num: true },
  { label: 'Touches', read: (s) => String(s.touches), num: true },
];

const statusWord = (s: MergeSide) =>
  companyStatus({ id: s.companyId, name: s.name, status: s.status, pausedAt: null, heldByTouchId: null, held: false, heldByReply: null }).label;

/**
 * Why the API suggests the side it does, in its own order (more touches, then more people, then the older record),
 * and what a merge does. Built from the counts on screen, so it never says more than the table shows.
 */
function whyLine(keeper: MergeSide, other: MergeSide): string {
  const because =
    keeper.touches !== other.touches
      ? `your ${count(keeper.touches, 'touch is', 'touches are')} there`
      : keeper.people !== other.people
        ? `${count(keeper.people, 'person is', 'people are')} on file there`
        : 'it is the older record';
  return (
    `${keeper.name} is suggested because ${because}. Merging keeps ${keeper.name}, folds ${other.name} into it, and sends ` +
    `anything found later under ${possessive(other.name)} name to ${keeper.name}. You can undo it from Resolved.`
  );
}

/**
 * The merge confirm (design 5.4, merge row): the actions at the top right of the row and, below the reason, the two
 * companies side by side. A row whose values differ is marked with the word "Differs" and `data-differs`, so color
 * is never the signal. The suggested side merges on M; the other one is a click with no key; Keep separate is O.
 * It sits in the grid of a Needs you row: the actions take the right column, the table the full width under the head.
 */
export function MergeCompare({
  item,
  onMerge,
  onKeepSeparate,
}: {
  item: ReviewItem;
  onMerge: (keep: Keep) => void;
  onKeepSeparate: () => void;
}) {
  const m = item.merge;
  if (!m) return null;
  const keep = m.suggestedKeep;
  const otherKeep: Keep = keep === 'a' ? 'b' : 'a';
  const keeper = m[keep];
  const other = m[otherKeep];
  const sides: [Keep, MergeSide][] = [['a', m.a], ['b', m.b]];

  return (
    <>
      <div className="col-start-3 row-span-2 row-start-1 flex items-start gap-1.5">
        <ActionButton label={`Merge into ${keeper.name}`} keyName="M" variant="outline" onClick={() => onMerge(keep)} />
        <ActionButton label={`Merge into ${other.name}`} variant="ghost" onClick={() => onMerge(otherKeep)} />
        <ActionButton label="Keep separate" keyName="O" variant="ghost" onClick={onKeepSeparate} />
      </div>
      <div className="col-span-2 col-start-2 grid gap-2 text-[13px]">
        {m.warning ? (
          <p className="flex items-start gap-1.5 text-xs text-warn">
            <TriangleAlert aria-hidden className="mt-px size-3.5 flex-none" />
            {m.warning}
          </p>
        ) : null}
        <div className="max-w-[760px] overflow-hidden rounded-lg border bg-card">
          <Table aria-label={`${mergeName(item)}, side by side`}>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="w-32">
                  <span className="sr-only">Fact</span>
                </TableHead>
                {sides.map(([k, s]) => (
                  <TableHead key={k} className="h-auto py-2 align-top font-semibold text-foreground">
                    <span className="flex items-center gap-2">
                      {s.name}
                      {k === keep ? <Badge variant="outline">Suggested</Badge> : null}
                    </span>
                    <span className="mt-0.5 block text-xs font-normal text-muted-foreground">{statusWord(s)}</span>
                  </TableHead>
                ))}
                <TableHead className="w-px">
                  <span className="sr-only">Same or different</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {FACTS.map((f) => {
                const a = f.read(m.a);
                const b = f.read(m.b);
                const differs = a !== b;
                return (
                  <TableRow key={f.label} data-differs={differs || undefined}>
                    <TableHead scope="row" className="h-auto py-2 font-normal whitespace-nowrap text-muted-foreground">
                      {f.label}
                    </TableHead>
                    {[a, b].map((v, i) => (
                      <TableCell
                        key={i}
                        className={cn('w-[40%] whitespace-normal', f.num && 'tabular-nums', differs ? 'font-medium' : 'text-muted-foreground')}
                      >
                        {v}
                      </TableCell>
                    ))}
                    <TableCell className="w-px">
                      {differs ? (
                        <span className="inline-flex items-center gap-1 text-xs font-medium whitespace-nowrap">
                          <EqualNot aria-hidden className="size-3.5" />
                          Differs
                        </span>
                      ) : null}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
        <p className="max-w-[760px] text-xs text-muted-foreground">{whyLine(keeper, other)}</p>
      </div>
    </>
  );
}
