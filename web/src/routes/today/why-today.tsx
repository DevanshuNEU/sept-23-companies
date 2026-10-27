import { Briefcase, Building, Check, FileCheck2, Globe, ShieldCheck, Sparkles, TrendingUp, Users, type LucideIcon } from 'lucide-react';
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
      <p className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1 text-base leading-6">
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

/** One reason as a small chip: an icon, then the API's words. The fresh one is the only coloured chip (the info hue). */
function ReasonChip({ icon: Icon, tone = 'plain', children }: { icon: LucideIcon; tone?: 'plain' | 'fresh'; children: string }) {
  return (
    <li
      data-reason={tone}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[13px] leading-5',
        tone === 'fresh' ? 'border-info-bd bg-info-bg text-info' : 'bg-subtle text-foreground',
      )}
    >
      <Icon aria-hidden className={cn('size-3.5 flex-none', tone === 'plain' && 'text-muted-foreground')} />
      {children}
    </li>
  );
}

/**
 * Why it should answer, as chips: the new opening, E-Verify, sponsorship, size and stage, recent hiring, then the
 * ranker's own reasons (each once: a reason that says what a chip already says is dropped). Every word is the API's.
 */
function reasonChips(q: CompanyQueue): { icon: LucideIcon; tone: 'plain' | 'fresh'; text: string }[] {
  const p = q.profile;
  const all: { icon: LucideIcon; tone: 'plain' | 'fresh'; text: string | null }[] = [
    { icon: Sparkles, tone: 'fresh', text: q.opening?.fresh ? q.opening.freshTag : null },
    { icon: ShieldCheck, tone: 'plain', text: p.everify.text },
    { icon: FileCheck2, tone: 'plain', text: p.sponsorship.text },
    { icon: Users, tone: 'plain', text: q.sizeLine },
    { icon: TrendingUp, tone: 'plain', text: q.hiring > 0 ? q.hiringLine : null },
    ...q.reasons.map((text) => ({ icon: Check, tone: 'plain' as const, text })),
  ];
  const seen = new Set<string>();
  const out: { icon: LucideIcon; tone: 'plain' | 'fresh'; text: string }[] = [];
  for (const c of all) {
    if (!c.text) continue;
    const key = c.text.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ ...c, text: c.text });
  }
  return out;
}

/** The judge's one-line reason in a quieter quote style, with its risk line; or that it has not judged this company. */
function Verdict({ q }: { q: CompanyQueue }) {
  const p = q.profile;
  if (q.judgeFit === null || p.verdict.text === null) {
    return (
      <p data-judge className="text-[13px] text-muted-foreground">
        {q.judgeLine}
      </p>
    );
  }
  return (
    <blockquote data-judge className="grid gap-1 border-l-2 pl-3.5 text-sm leading-[1.6]">
      <p className="text-foreground/90">{p.verdict.text}</p>
      {p.risk ? <p className="text-[13px] text-muted-foreground">{`Risk: ${p.risk}`}</p> : null}
    </blockquote>
  );
}

/**
 * Why today, first in the panel (top picks): the opening and its age, why the company should answer as chips, and the
 * profile judge's one-line reason and risk (or that it has not judged the company). Every line is the API's.
 */
export function WhyToday({ q }: { q: CompanyQueue }) {
  const chips = reasonChips(q);
  return (
    <section aria-label="Why today" className="grid gap-4 rounded-xl border bg-card px-5 py-4.5 shadow-xs">
      <div className="grid gap-2">
        <h3 className="text-xs font-medium text-muted-foreground">Why today</h3>
        {q.opening ? <OpeningLine opening={q.opening} /> : <p className="text-[15px] leading-6 text-muted-foreground">No open role at your level. {q.whyToday}</p>}
      </div>
      {chips.length > 0 ? (
        <ul aria-label="Why it should answer" className="flex flex-wrap gap-2">
          {chips.map((c) => (
            <ReasonChip key={c.text} icon={c.icon} tone={c.tone}>
              {c.text}
            </ReasonChip>
          ))}
        </ul>
      ) : null}
      <Verdict q={q} />
    </section>
  );
}

/** The three research links: Careers (O), the company's website, its LinkedIn page (a company search; never fetched). */
export function ResearchLinks({ q, keys = true, variant = 'outline' }: { q: CompanyQueue; keys?: boolean; variant?: 'outline' | 'secondary' }) {
  const careers = safeHref(q.careersUrl);
  const site = safeHref(q.websiteUrl);
  const linkedin = safeHref(q.linkedinCompanyUrl);
  return (
    <div role="group" aria-label="Research" className="flex flex-wrap items-center gap-2">
      {careers ? (
        <Button asChild variant={variant} size="sm" className={MAIN}>
          <a href={careers} target="_blank" rel="noopener noreferrer" aria-keyshortcuts={keys ? 'o' : undefined}>
            <Briefcase aria-hidden />
            Careers
            {keys ? <KeyHint k="O" /> : null}
          </a>
        </Button>
      ) : null}
      {site ? (
        <Button asChild variant={variant} size="sm" className={MAIN}>
          <a href={site} target="_blank" rel="noopener noreferrer">
            <Globe aria-hidden />
            Website
          </a>
        </Button>
      ) : null}
      {linkedin ? (
        <Button asChild variant={variant} size="sm" className={MAIN}>
          <a href={linkedin} target="_blank" rel="noopener noreferrer">
            <Building aria-hidden />
            LinkedIn page
          </a>
        </Button>
      ) : null}
    </div>
  );
}
