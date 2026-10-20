import { Ban, Check, ChevronDown, TriangleAlert } from 'lucide-react';
import type { Check as CheckResult, CheckFix, RoleKind } from '@/api';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { cn } from '@/lib/utils';

const KIND_LABELS: [RoleKind, string][] = [
  ['recruiter', 'Recruiter'],
  ['engineer', 'Engineer'],
  ['manager', 'Manager'],
  ['founder', 'Founder'],
  ['alum', 'Alum'],
  ['other', 'Other'],
];

const FIX_LABELS: Record<Exclude<CheckFix['kind'], 'role_kind'>, string> = {
  compare: 'Compare drafts',
  review_item: 'Fix in Needs you',
  no_go: 'Open no-go entry',
};

type RoleKindControl = { value: RoleKind; onChange: (k: RoleKind) => void };

function Fix({ fix, onFix, roleKind }: { fix: CheckFix; onFix: (fix: CheckFix) => void; roleKind?: RoleKindControl }) {
  if (fix.kind === 'role_kind') {
    if (!roleKind) return null;
    return (
      <Select value={roleKind.value} onValueChange={(v) => roleKind.onChange(v as RoleKind)}>
        <SelectTrigger size="sm" aria-label="Role kind" className="text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {KIND_LABELS.map(([kind, label]) => (
            <SelectItem key={kind} value={kind}>
              {label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  }
  return (
    <Button type="button" variant="outline" size="sm" onClick={() => onFix(fix)}>
      {FIX_LABELS[fix.kind]}
    </Button>
  );
}

function Row({ check, onFix, roleKind }: { check: CheckResult; onFix: (fix: CheckFix) => void; roleKind?: RoleKindControl }) {
  const block = check.severity === 'block';
  const Icon = block ? Ban : TriangleAlert;
  return (
    <li
      data-severity={check.severity}
      className={cn(
        'grid grid-cols-[16px_1fr_auto] items-start gap-x-2.5 gap-y-0.5 border-b px-3 py-2.5 text-[13px] last:border-b-0',
        block ? 'bg-bad-bg' : 'bg-warn-bg',
      )}
    >
      <Icon aria-hidden className={cn('mt-0.5 size-4', block ? 'text-bad' : 'text-warn')} />
      <span className="font-medium">
        <span className="sr-only">{block ? 'Blocking: ' : 'Warning: '}</span>
        {check.title}
      </span>
      {check.detail ? <span className="col-start-2 text-muted-foreground">{check.detail}</span> : null}
      {check.fix ? (
        <span className="col-start-3 row-span-2 row-start-1 self-center">
          <Fix fix={check.fix} onFix={onFix} roleKind={roleKind} />
        </span>
      ) : null}
    </li>
  );
}

/** Spec 5.3, Check results: block rows, then warnings, then one collapsed line for what passed. */
export function CheckList({
  checks,
  onFix,
  roleKind,
}: {
  checks: CheckResult[];
  onFix: (fix: CheckFix) => void;
  roleKind?: RoleKindControl;
}) {
  const blocks = checks.filter((c) => c.severity === 'block');
  const warns = checks.filter((c) => c.severity === 'warn');
  const passed = checks.filter((c) => c.severity === 'pass');
  const flagged = [...blocks, ...warns];
  return (
    <div aria-label="Check results" role="group" className="overflow-hidden rounded-md border">
      {flagged.length > 0 ? (
        <ul>
          {flagged.map((c) => (
            <Row key={c.code} check={c} onFix={onFix} roleKind={roleKind} />
          ))}
        </ul>
      ) : null}
      {passed.length > 0 ? (
        <Collapsible className="group/passed">
          <CollapsibleTrigger className="flex w-full cursor-pointer items-center gap-2.5 px-3 py-2.5 text-left text-[13px] text-muted-foreground outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">
            <Check aria-hidden className="size-4 text-ok" />
            {passed.length === 1 ? '1 check passed' : `${passed.length} checks passed`}
            <ChevronDown aria-hidden className="ml-auto size-4 transition-transform group-data-[state=open]/passed:rotate-180" />
          </CollapsibleTrigger>
          <CollapsibleContent>
            <ul className="grid gap-1 pr-3 pb-3 pl-[38px] text-[13px] text-muted-foreground">
              {passed.map((c) => (
                <li key={c.code}>{c.title}</li>
              ))}
            </ul>
          </CollapsibleContent>
        </Collapsible>
      ) : null}
    </div>
  );
}
