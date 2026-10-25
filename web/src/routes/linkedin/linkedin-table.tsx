import { Fragment } from 'react';
import { Link } from 'react-router';
import { ChevronLeft, ChevronRight, Copy, ExternalLink } from 'lucide-react';
import type { Id, IsoTime, LinkedInGroup, LinkedInPerson, LinkedInReason, LinkedInStep, RoleKind } from '@/api';
import { LinkedInSteps } from '@/components/linkedin-steps';
import { StatusBadge } from '@/components/status-badge';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Kbd } from '@/components/ui/kbd';
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import type { RowProps } from '@/keyboard/list-nav';
import { replyKindStatus } from '@/lib/status';
import { cn } from '@/lib/utils';
import { NEXT, nextStep } from './linkedin-steps';
import { safeHref } from '@/lib/safe-href';

/** One company and the people of it that the current tab and filter show. */
export interface ShownGroup {
  group: LinkedInGroup;
  people: LinkedInPerson[];
}

/**
 * Column widths follow the table's own width (a container query), so they hold with the sidebar open or shut.
 * Wide (1000 px and up): every column on one line. Compact: the progress words go under the dots, "Wait for Priya?"
 * under the button, and a long Why LinkedIn wraps to two lines. Fold (under 760 px): Why LinkedIn moves under the
 * person and its column shrinks to nothing (it stays in the table, so the company rows' colSpan still matches).
 * Person takes what is left.
 */
const COL = {
  kind: 'w-[96px] @min-[1000px]:w-[104px]',
  why: 'w-[156px] @min-[1000px]:w-[184px] @max-[759px]:invisible @max-[759px]:w-0 @max-[759px]:px-0',
  progress: 'w-[148px] @min-[1000px]:w-[236px]',
  next: 'w-[188px] @min-[1000px]:w-[260px]',
};
// Sticky under the top bar. The line under it is drawn inside the cell, so it moves with the header.
const HEAD = 'sticky top-0 z-10 h-9 bg-subtle px-3 text-muted-foreground shadow-[inset_0_-1px_0_var(--border)]';

const KIND: Record<RoleKind, string> = {
  engineer: 'Engineer',
  manager: 'Manager',
  founder: 'Founder',
  recruiter: 'Recruiter',
  alum: 'Alum',
  other: 'Other',
};

function why(reason: LinkedInReason, emailed: number): string {
  if (reason === 'past_budget') return `Past the ${emailed} emailed`;
  if (reason === 'no_email') return 'No email found';
  return 'Alum: LinkedIn first';
}

const initials = (name: string) => {
  const words = name.split(/\s+/).filter(Boolean);
  return `${words[0]?.[0] ?? ''}${words.length > 1 ? (words[words.length - 1]?.[0] ?? '') : ''}`.toUpperCase();
};

/** "Priya" from "Priya Natarajan". */
const firstName = (name: string) => name.split(/\s+/)[0] ?? name;

function GroupRow({ group, onCopy }: { group: LinkedInGroup; onCopy: (g: LinkedInGroup) => void }) {
  return (
    <TableRow className="bg-subtle hover:bg-subtle">
      <TableCell colSpan={5} className="h-11 px-3 py-1.5 whitespace-normal">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <Link to={`/companies/${group.company.id}`} className="text-sm font-medium hover:underline">
            {group.company.name}
          </Link>
          {group.heldBy ? (
            <StatusBadge dot status={{ label: `Held: ${firstName(group.heldBy.personName)} replied by email`, tone: 'ok' }} />
          ) : null}
          {group.role ? (
            <>
              <span className="text-sm text-muted-foreground">Role to name</span>
              <b className="max-w-full truncate text-sm font-medium" title={group.role.title}>
                {group.role.title}
              </b>
              <Button type="button" variant="ghost" size="sm" className="h-7" onClick={() => onCopy(group)}>
                <Copy aria-hidden />
                Copy
                <Kbd>C</Kbd>
              </Button>
            </>
          ) : (
            <span className="text-sm text-muted-foreground">No role to name yet</span>
          )}
          <span className="ml-auto text-xs text-muted-foreground tabular-nums">{`${group.emailed} emailed, ${group.onLinkedIn} here`}</span>
        </div>
      </TableCell>
    </TableRow>
  );
}

function PersonRow({
  person: p,
  group,
  asOf,
  rowProps,
  onLog,
}: {
  person: LinkedInPerson;
  group: LinkedInGroup;
  asOf: IsoTime;
  rowProps: RowProps;
  onLog: (p: LinkedInPerson, step: LinkedInStep) => void;
}) {
  const next = nextStep(p);
  const held = group.heldBy;
  const reason = why(p.reason, group.emailed);
  return (
    <TableRow
      {...rowProps}
      // The scroll margins keep a row moved to by j or k clear of the sticky header and footer.
      className="cursor-default scroll-mt-10 scroll-mb-12 data-[active=true]:bg-muted data-[active=true]:shadow-[inset_2px_0_0_var(--foreground)]"
    >
      <TableCell className="px-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <span
            aria-hidden
            className="grid size-7 flex-none place-items-center rounded-full bg-muted text-[11px] font-semibold text-muted-foreground"
          >
            {initials(p.person.name)}
          </span>
          <div className="min-w-0">
            <div className="truncate font-medium" title={p.person.name}>
              {p.person.name}
            </div>
            <div className="truncate text-xs text-muted-foreground" title={p.person.title}>
              {p.person.title}
            </div>
            <div className="hidden truncate text-xs text-muted-foreground @max-[759px]:block">{reason}</div>
          </div>
        </div>
      </TableCell>
      <TableCell className="px-3">
        <Badge variant="outline">{KIND[p.person.roleKind]}</Badge>
      </TableCell>
      <TableCell className="px-3 text-sm whitespace-normal @max-[759px]:px-0">
        <span className="line-clamp-2 @max-[759px]:hidden" title={reason}>
          {reason}
        </span>
      </TableCell>
      <TableCell className="px-3">
        <LinkedInSteps
          steps={p.steps}
          asOf={asOf}
          className="@max-[999px]:flex-col @max-[999px]:items-start @max-[999px]:gap-1"
          labelClassName="@max-[999px]:ml-0"
        />
      </TableCell>
      <TableCell className="px-3">
        <div
          className={cn(
            'flex min-w-0 items-center justify-end gap-1',
            held && next !== null && '@max-[999px]:flex-col @max-[999px]:items-end @max-[999px]:gap-0.5',
          )}
        >
          {next === null ? (
            p.replyKind ? <StatusBadge status={replyKindStatus[p.replyKind]} /> : null
          ) : (
            <>
              {held ? (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span
                      tabIndex={0}
                      className="mr-1 min-w-0 truncate text-xs text-muted-foreground @max-[999px]:order-last @max-[999px]:mr-0 @max-[999px]:max-w-full"
                    >
                      {`Wait for ${firstName(held.personName)}?`}
                    </span>
                  </TooltipTrigger>
                  <TooltipContent>
                    {`A reply holds email, not LinkedIn. Usually worth waiting until the conversation with ${firstName(held.personName)} settles.`}
                  </TooltipContent>
                </Tooltip>
              ) : safeHref(p.person.linkedinUrl) ? (
                <Button asChild variant="ghost" size="icon-sm" className="size-7">
                  <a href={safeHref(p.person.linkedinUrl)} target="_blank" rel="noopener noreferrer" aria-label="Open LinkedIn profile">
                    <ExternalLink aria-hidden />
                  </a>
                </Button>
              ) : null}
              <Button type="button" variant="outline" size="sm" className="h-7 flex-none" onClick={() => onLog(p, next)}>
                {NEXT[next].label}
                <Kbd>{NEXT[next].key}</Kbd>
              </Button>
            </>
          )}
        </div>
      </TableCell>
    </TableRow>
  );
}

/** Where this page sits in the list, for the footer. */
export interface PageInfo {
  page: number;
  count: number;
  from: number;
  to: number;
  total: number;
  onPrevious: () => void;
  onNext: () => void;
}

/** The calm footer: who is showing, and Previous, the page and Next. It sticks to the bottom while the page scrolls. */
function PageFooter({ info }: { info: PageInfo }) {
  const { page, count, from, to, total } = info;
  return (
    <div className="sticky bottom-0 z-10 flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 border-t bg-subtle px-3 py-1.5 text-xs text-muted-foreground">
      <span className="tabular-nums">{`Showing ${from} to ${to} of ${total} ${total === 1 ? 'person' : 'people'}`}</span>
      {count > 1 ? (
        <nav aria-label="Pages" className="ml-auto flex items-center gap-1">
          <Button type="button" variant="ghost" size="sm" className="h-7" disabled={page <= 1} onClick={info.onPrevious}>
            <ChevronLeft aria-hidden />
            Previous
            <Kbd>[</Kbd>
          </Button>
          <span className="px-2 font-medium text-foreground tabular-nums">{`Page ${page} of ${count}`}</span>
          <Button type="button" variant="ghost" size="sm" className="h-7" disabled={page >= count} onClick={info.onNext}>
            Next
            <Kbd>]</Kbd>
            <ChevronRight aria-hidden />
          </Button>
        </nav>
      ) : null}
    </div>
  );
}

/**
 * One table grouped by company (design 5.2): a header row per company, then its people, one page of companies at a
 * time. The card clips instead of hiding overflow, so the header and footer can stick to the page's scroll.
 */
export function LinkedInTable({
  groups,
  asOf,
  rowProps,
  onLog,
  onCopy,
  pageInfo,
}: {
  groups: ShownGroup[];
  asOf: IsoTime;
  rowProps: (id: Id) => RowProps;
  onLog: (p: LinkedInPerson, step: LinkedInStep) => void;
  onCopy: (g: LinkedInGroup) => void;
  pageInfo: PageInfo;
}) {
  return (
    <div className="@container overflow-clip rounded-lg border bg-card">
      <table data-slot="table" className="w-full table-fixed text-[13px]">
        <TableHeader className="[&_tr]:border-b-0">
          <TableRow className="hover:bg-transparent">
            <TableHead className={HEAD}>Person</TableHead>
            <TableHead className={cn(HEAD, COL.kind)}>Kind</TableHead>
            <TableHead className={cn(HEAD, COL.why)}>Why LinkedIn</TableHead>
            <TableHead className={cn(HEAD, COL.progress)}>Progress</TableHead>
            <TableHead className={cn(HEAD, COL.next, 'text-right')}>Next</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {groups.map(({ group, people }) => (
            <Fragment key={group.company.id}>
              <GroupRow group={group} onCopy={onCopy} />
              {people.map((p) => (
                <PersonRow key={p.person.id} person={p} group={group} asOf={asOf} rowProps={rowProps(p.person.id)} onLog={onLog} />
              ))}
            </Fragment>
          ))}
        </TableBody>
      </table>
      <PageFooter info={pageInfo} />
    </div>
  );
}
