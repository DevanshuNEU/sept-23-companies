import { useId, useState, type ReactNode } from 'react';
import { Boxes, Briefcase, ChevronRight, ExternalLink, History, Link2, MapPin, ShieldCheck, TrendingUp, Users, UsersRound } from 'lucide-react';
import type { CompanyProfile, CompanyQueue, PlanRow, ProfileValue } from '@/api';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { safeHref } from '@/lib/safe-href';
import { cn } from '@/lib/utils';

const link =
  'rounded-sm underline decoration-foreground/30 underline-offset-3 outline-none hover:decoration-foreground focus-visible:ring-[3px] focus-visible:ring-ring/70';

/** A section: a small icon header, then its facts. A region so a screen reader can jump to it. */
function Section({ icon: Icon, title, children }: { icon: typeof Boxes; title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="grid gap-1.5 border-t py-4 first:border-t-0 first:pt-0">
      <h3 id={id} className="flex items-center gap-2 text-[13px] leading-5 font-semibold">
        <Icon aria-hidden className="size-3.5 text-muted-foreground" />
        {title}
      </h3>
      {children}
    </section>
  );
}

/**
 * One fact: its label, its words (or a muted "Not known", or the `unknown` words given), and in small muted text how
 * sure it is and where it comes from. The last column is kept for a future pencil beside an unknown value (editing is
 * not built yet).
 */
function Fact({
  id,
  label,
  v,
  unknown = 'Not known',
  children,
}: {
  id: string;
  label: string;
  v?: ProfileValue;
  unknown?: string;
  children?: ReactNode;
}) {
  const known = v ? v.text !== null : true;
  return (
    <div data-fact={id} className="grid grid-cols-[76px_minmax(0,1fr)_auto] items-baseline gap-x-3 text-sm leading-[1.55]">
      <span className="text-[13px] text-muted-foreground">{label}</span>
      <div className="grid min-w-0">
        <span className={cn('min-w-0 break-words', !known && 'text-muted-foreground')}>{children ?? (known ? v?.text : unknown)}</span>
        {known && v?.note ? <span className="text-xs leading-5 text-muted-foreground">{v.note}</span> : null}
      </div>
      <span data-slot="edit" />
    </div>
  );
}

function Roles({ roles }: { roles: CompanyProfile['roles'] }) {
  if (roles.length === 0) return <p className="text-sm text-muted-foreground">None open at your level</p>;
  return (
    <ul aria-label="Open roles at your level" className="grid gap-2.5">
      {roles.map((r) => {
        const href = safeHref(r.url);
        return (
          <li key={r.postingId} className="grid gap-0.5 text-sm leading-[1.5]">
            {href ? (
              <a href={href} target="_blank" rel="noopener noreferrer" className={cn(link, 'w-fit font-medium')}>
                {r.title}
              </a>
            ) : (
              <span className="font-medium">{r.title}</span>
            )}
            <span className="text-xs leading-5 text-muted-foreground">{[r.level, r.age, r.location].filter(Boolean).join(', ')}</span>
          </li>
        );
      })}
    </ul>
  );
}

function OutLink({ href, children }: { href: string | null; children: string }) {
  const safe = safeHref(href);
  return safe ? (
    <a href={safe} target="_blank" rel="noopener noreferrer" className={cn(link, 'inline-flex items-center gap-1')}>
      {children}
      <ExternalLink aria-hidden className="size-3 text-muted-foreground" />
    </a>
  ) : null;
}

/** The one line the fold shows while closed: what is known about stage, size and place, or that little is. */
function profileSummary(p: CompanyProfile): string {
  const known = [p.stage.text, p.headcount.text, p.hq.text].filter((t): t is string => t !== null);
  return known.length > 0 ? known.join(', ') : 'Not much known yet';
}

/**
 * The profile of a pick: the things that do not change. Every line is the API's (`queue.profile`), each with its
 * confidence and source in small muted text, and "Not known" (muted, never red) where nothing is. Nothing here edits.
 */
export function ProfilePanel({ row, className }: { row: PlanRow; className?: string }) {
  const q: CompanyQueue | null = row.queue;
  if (!q) return null;
  const p = q.profile;
  return (
    <div className={cn('grid', className)}>
      <Section icon={Boxes} title="What they build">
        <div data-fact="builds" className="grid gap-1.5">
          {p.builds.text !== null ? (
            <>
              <p className="text-sm leading-[1.55]">{p.builds.text}</p>
              {p.builds.note ? <p className="text-xs leading-5 text-muted-foreground">{p.builds.note}</p> : null}
            </>
          ) : (
            <p className="text-sm text-muted-foreground">Not judged yet</p>
          )}
          {p.domains.length > 0 ? (
            <ul aria-label="Domains" className="flex flex-wrap gap-1.5">
              {p.domains.map((d) => (
                <li key={d} className="rounded-md bg-muted px-2 py-0.5 text-xs leading-5 text-foreground">
                  {d}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </Section>

      <Section icon={TrendingUp} title="Stage and funding">
        <Fact id="stage" label="Stage" v={p.stage} />
        <Fact id="funding" label="Funding" v={p.funding} />
      </Section>

      <Section icon={Users} title="Headcount">
        {/* The size gate keeps a giant or a likely large company off Today, so an unknown size here was never looked up. */}
        <Fact id="headcount" label="People" v={p.headcount} unknown="Not checked" />
      </Section>

      <Section icon={MapPin} title="Where">
        <Fact id="hq" label="HQ" v={p.hq} />
        <Fact id="roleplaces" label="Roles in" v={p.rolePlaces} />
      </Section>

      <Section icon={ShieldCheck} title="Visa">
        <Fact id="everify" label="E-Verify" v={p.everify} />
        <Fact id="sponsorship" label="Sponsors" v={p.sponsorship} />
        <Fact id="lca" label="H-1B" v={p.lca} />
      </Section>

      <Section icon={Briefcase} title="Open roles at your level">
        <Roles roles={p.roles} />
      </Section>

      <Section icon={UsersRound} title="People on record">
        <Fact id="people" label="People" v={p.people} />
      </Section>

      <Section icon={History} title="Your history">
        {p.history.applied || q.lastTouch || p.history.passes.length > 0 ? (
          <ul className="grid gap-1 text-sm leading-[1.55]">
            {p.history.applied ? <li>{p.history.applied}</li> : null}
            {q.lastTouch ? <li>{q.lastTouch}</li> : null}
            {p.history.passes.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">Nothing yet</p>
        )}
      </Section>

      <Section icon={Link2} title="Links">
        <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-sm">
          <OutLink href={q.careersUrl}>Careers</OutLink>
          <OutLink href={q.websiteUrl}>Website</OutLink>
          <OutLink href={q.linkedinCompanyUrl}>LinkedIn page</OutLink>
        </div>
      </Section>
    </div>
  );
}

/**
 * Under 1280 px the profile folds under the main column: one quiet row, "Company profile", with a line of what is
 * known. It opens to the same facts the side panel shows, so nothing is lost on a narrow window.
 */
export function ProfileFold({ row }: { row: PlanRow }) {
  const [open, setOpen] = useState(false);
  if (!row.queue) return null;
  return (
    <Collapsible open={open} onOpenChange={setOpen} asChild>
      <section aria-label="Company profile" className="rounded-xl border bg-card">
        <CollapsibleTrigger className="flex w-full items-center gap-2 rounded-xl px-4 py-3 text-left outline-none hover:bg-muted/50 focus-visible:ring-[3px] focus-visible:ring-ring/70">
          <ChevronRight aria-hidden className={cn('size-4 flex-none text-muted-foreground transition-transform duration-150', open && 'rotate-90')} />
          <span className="text-sm font-semibold">Company profile</span>
          <span className="min-w-0 flex-1 truncate text-[13px] text-muted-foreground">{profileSummary(row.queue.profile)}</span>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <ProfilePanel row={row} className="border-t px-4 pt-4 pb-2" />
        </CollapsibleContent>
      </section>
    </Collapsible>
  );
}
