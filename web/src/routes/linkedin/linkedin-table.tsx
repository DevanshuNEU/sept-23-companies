import { Fragment } from 'react';
import { Link } from 'react-router';
import { Copy, ExternalLink } from 'lucide-react';
import type { Id, IsoTime, LinkedInGroup, LinkedInPerson, LinkedInReason, LinkedInStep, RoleKind } from '@/api';
import { LinkedInSteps } from '@/components/linkedin-steps';
import { StatusBadge } from '@/components/status-badge';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Kbd } from '@/components/ui/kbd';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import type { RowProps } from '@/keyboard/list-nav';
import { replyKindStatus } from '@/lib/status';
import { NEXT, nextStep } from './linkedin-steps';
import { safeHref } from '@/lib/safe-href';

/** One company and the people of it that the current tab and filter show. */
export interface ShownGroup {
  group: LinkedInGroup;
  people: LinkedInPerson[];
}

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
      <TableCell colSpan={5} className="h-11 px-3">
        <div className="flex items-center gap-2">
          <Link to={`/companies/${group.company.id}`} className="text-sm font-medium hover:underline">
            {group.company.name}
          </Link>
          {group.heldBy ? (
            <StatusBadge dot status={{ label: `Held: ${firstName(group.heldBy.personName)} replied by email`, tone: 'ok' }} />
          ) : null}
          {group.role ? (
            <>
              <span className="text-sm text-muted-foreground">Role to name</span>
              <b className="text-sm font-medium">{group.role.title}</b>
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
  return (
    <TableRow
      {...rowProps}
      className="cursor-default data-[active=true]:bg-muted data-[active=true]:shadow-[inset_2px_0_0_var(--foreground)]"
    >
      <TableCell className="px-3">
        <div className="flex items-center gap-2.5">
          <span
            aria-hidden
            className="grid size-7 flex-none place-items-center rounded-full bg-muted text-[11px] font-semibold text-muted-foreground"
          >
            {initials(p.person.name)}
          </span>
          <div className="min-w-0">
            <div className="font-medium">{p.person.name}</div>
            <div className="text-xs text-muted-foreground">{p.person.title}</div>
          </div>
        </div>
      </TableCell>
      <TableCell className="px-3">
        <Badge variant="outline">{KIND[p.person.roleKind]}</Badge>
      </TableCell>
      <TableCell className="px-3 text-sm">{why(p.reason, group.emailed)}</TableCell>
      <TableCell className="px-3">
        <LinkedInSteps steps={p.steps} asOf={asOf} />
      </TableCell>
      <TableCell className="w-px px-3">
        <div className="flex items-center justify-end gap-1">
          {next === null ? (
            p.replyKind ? <StatusBadge status={replyKindStatus[p.replyKind]} /> : null
          ) : (
            <>
              {held ? (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span tabIndex={0} className="mr-1 text-xs whitespace-nowrap text-muted-foreground">
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
              <Button type="button" variant="outline" size="sm" className="h-7" onClick={() => onLog(p, next)}>
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

/** One table grouped by company (design 5.2): a header row per company, then its people. */
export function LinkedInTable({
  groups,
  asOf,
  rowProps,
  onLog,
  onCopy,
}: {
  groups: ShownGroup[];
  asOf: IsoTime;
  rowProps: (id: Id) => RowProps;
  onLog: (p: LinkedInPerson, step: LinkedInStep) => void;
  onCopy: (g: LinkedInGroup) => void;
}) {
  return (
    <div className="overflow-hidden rounded-lg border bg-card">
      <Table className="text-[13px]">
        <TableHeader className="bg-subtle">
          <TableRow className="hover:bg-transparent">
            <TableHead className="h-9 px-3 text-muted-foreground">Person</TableHead>
            <TableHead className="h-9 px-3 text-muted-foreground">Kind</TableHead>
            <TableHead className="h-9 px-3 text-muted-foreground">Why LinkedIn</TableHead>
            <TableHead className="h-9 px-3 text-muted-foreground">Progress</TableHead>
            <TableHead className="h-9 w-px px-3 text-right text-muted-foreground">Next</TableHead>
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
      </Table>
    </div>
  );
}
