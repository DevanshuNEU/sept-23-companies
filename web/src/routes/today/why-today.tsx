import { Briefcase, Building, Globe, Sparkles } from 'lucide-react';
import type { CompanyQueue, QueueOpening } from '@/api';
import { Button } from '@/components/ui/button';
import { safeHref } from '@/lib/safe-href';
import { cn } from '@/lib/utils';
import { KeyHint, MAIN } from './plan-panel';

const link =
  'rounded-sm underline decoration-foreground/30 underline-offset-3 outline-none hover:decoration-foreground focus-visible:ring-[3px] focus-visible:ring-ring/70';

/** "New opening, 6 hours ago": the small tag a pick with a new opening carries, in the calm info hue. */
export function FreshTag({ opening, className }: { opening: QueueOpening | null | undefined; className?: string }) {
  if (!opening?.fresh || !opening.freshTag) return null;
  return (
    <span data-fresh className={cn('inline-flex w-fit items-center gap-1 rounded-sm bg-info-bg px-1.5 text-xs leading-5 font-medium text-info', className)}>
      <Sparkles aria-hidden className="size-3" />
      {opening.freshTag}
    </span>
  );
}

/** The opening as one line: its title (a link to the posting), its level and its age, then the applied note. */
export function OpeningLine({ opening }: { opening: QueueOpening }) {
  const href = safeHref(opening.url);
  return (
    <div className="grid gap-1">
      <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-base leading-6">
        {href ? (
          <a href={href} target="_blank" rel="noopener noreferrer" className={cn(link, 'font-medium')}>
            {opening.title}
          </a>
        ) : (
          <b className="font-medium">{opening.title}</b>
        )}
        <span className="text-[13px] text-muted-foreground">{[opening.level, opening.age].filter(Boolean).join(', ')}</span>
      </p>
      {opening.appliedNote ? <p className="text-[13px] text-muted-foreground">{opening.appliedNote}</p> : null}
    </div>
  );
}

/**
 * Why today, first in the panel (top picks): the opening and its age, why the company should answer (the ranker's
 * reasons, its size and stage, recent hiring), where it stands on a visa, and the profile judge's one line (what the
 * company builds, its fit and the risk, or "not judged yet"). Every line is the API's.
 */
export function WhyToday({ q, compact = false }: { q: CompanyQueue; compact?: boolean }) {
  const answer = [...q.reasons, ...(q.sizeLine ? [q.sizeLine] : []), ...(q.hiring > 0 ? [q.hiringLine] : [])];
  return (
    <section aria-label="Why today" className={cn('grid gap-3', !compact && 'rounded-lg border px-4 py-3.5')}>
      <div className="grid gap-1.5">
        {!compact ? <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Why today</h3> : null}
        <FreshTag opening={q.opening} />
        {q.opening ? <OpeningLine opening={q.opening} /> : <p className="text-base leading-6 text-muted-foreground">No open role at your level. {q.whyToday}</p>}
      </div>
      <dl className="grid gap-1.5 text-[13px]">
        <div className="grid grid-cols-[132px_minmax(0,1fr)] items-baseline gap-x-4 max-[700px]:grid-cols-1">
          <dt className="text-muted-foreground">Why it should answer</dt>
          <dd>
            <ul className="grid gap-0.5">
              {answer.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          </dd>
        </div>
        <div className="grid grid-cols-[132px_minmax(0,1fr)] items-baseline gap-x-4 max-[700px]:grid-cols-1">
          <dt className="text-muted-foreground">Visa</dt>
          <dd>{q.sponsorshipLine}</dd>
        </div>
      </dl>
      <p data-judge className={cn('text-[13px]', q.judgeFit === null && 'text-muted-foreground')}>
        {q.judgeLine}
      </p>
    </section>
  );
}

/** The three research links: Careers (O), the company's website, its LinkedIn page (a company search; never fetched). */
export function ResearchLinks({ q, keys = true }: { q: CompanyQueue; keys?: boolean }) {
  const careers = safeHref(q.careersUrl);
  const site = safeHref(q.websiteUrl);
  const linkedin = safeHref(q.linkedinCompanyUrl);
  return (
    <div role="group" aria-label="Research" className="flex flex-wrap items-center gap-2">
      {careers ? (
        <Button asChild variant="outline" size="sm" className={MAIN}>
          <a href={careers} target="_blank" rel="noopener noreferrer" aria-keyshortcuts={keys ? 'o' : undefined}>
            <Briefcase aria-hidden />
            Careers
            {keys ? <KeyHint k="O" /> : null}
          </a>
        </Button>
      ) : null}
      {site ? (
        <Button asChild variant="outline" size="sm" className={MAIN}>
          <a href={site} target="_blank" rel="noopener noreferrer">
            <Globe aria-hidden />
            Website
          </a>
        </Button>
      ) : null}
      {linkedin ? (
        <Button asChild variant="outline" size="sm" className={MAIN}>
          <a href={linkedin} target="_blank" rel="noopener noreferrer">
            <Building aria-hidden />
            LinkedIn page
          </a>
        </Button>
      ) : null}
    </div>
  );
}
