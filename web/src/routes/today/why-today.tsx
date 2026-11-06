import { Briefcase, Building, Globe } from 'lucide-react';
import type { CompanyQueue, QueueOpening } from '@/api';
import { Button } from '@/components/ui/button';
import { safeHref } from '@/lib/safe-href';
import { cn } from '@/lib/utils';
import { KeyHint, MAIN } from './plan-panel';

const link =
  'rounded-sm underline decoration-foreground/30 underline-offset-3 outline-none hover:decoration-foreground focus-visible:ring-[3px] focus-visible:ring-ring/70';

/** "New opening, 6 hours ago": a pick with a new opening says so in plain words, led by a small solid dot. No hue. */
export function FreshTag({ opening, className }: { opening: QueueOpening | null | undefined; className?: string }) {
  if (!opening?.fresh || !opening.freshTag) return null;
  return (
    <span data-fresh className={cn('inline-flex w-fit items-center gap-1.5 text-xs leading-5 font-medium text-foreground', className)}>
      <span aria-hidden className="size-1.5 rounded-full bg-foreground" />
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

/** One reason as a plain line of text, no pill and no icon. The fresh one leads with a solid dot and reads in the main colour. */
function Reason({ tone = 'plain', children }: { tone?: 'plain' | 'fresh'; children: string }) {
  return (
    <li data-reason={tone} className={cn('inline-flex items-center gap-1.5', tone === 'fresh' && 'font-medium text-foreground')}>
      {tone === 'fresh' ? <span aria-hidden className="size-1.5 rounded-full bg-foreground" /> : null}
      {children}
    </li>
  );
}

const REPEATS_OPENING = /^(open role at your level|new opening)/i;

/**
 * Why it should answer, as plain lines: the new opening, E-Verify, sponsorship, size and stage, recent hiring, then the
 * ranker's own reasons (each once: a reason that says what a line already says is dropped). Every word is the API's.
 */
function reasons(q: CompanyQueue): { tone: 'plain' | 'fresh'; text: string }[] {
  const p = q.profile;
  const all: { tone: 'plain' | 'fresh'; text: string | null }[] = [
    { tone: 'fresh', text: q.opening?.fresh ? q.opening.freshTag : null },
    { tone: 'plain', text: p.everify.text },
    { tone: 'plain', text: p.sponsorship.text },
    { tone: 'plain', text: q.sizeLine },
    { tone: 'plain', text: q.hiring > 0 ? q.hiringLine : null },
    // "Open role at your level" and "New opening today" only repeat the opening line and the new tag above them.
    ...q.reasons.filter((text) => !REPEATS_OPENING.test(text)).map((text) => ({ tone: 'plain' as const, text })),
  ];
  const seen = new Set<string>();
  const out: { tone: 'plain' | 'fresh'; text: string }[] = [];
  for (const c of all) {
    if (!c.text) continue;
    const key = c.text.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ tone: c.tone, text: c.text });
  }
  return out;
}

/** The judge's one-line reason in a quieter quote style, with its risk line. Nothing when it has not judged this company. */
function Verdict({ q }: { q: CompanyQueue }) {
  const p = q.profile;
  // Not judged yet is said once, in the header ("What they build is not known yet") and the profile, not a third time here.
  if (q.judgeFit === null || p.verdict.text === null) return null;
  return (
    <blockquote data-judge className="grid gap-1 border-l-2 pl-3.5 text-sm leading-[1.6]">
      <p className="text-foreground/90">{p.verdict.text}</p>
      {p.risk ? <p className="text-[13px] text-muted-foreground">{`Risk: ${p.risk}`}</p> : null}
    </blockquote>
  );
}

/**
 * Why today, first in the panel (top picks): the opening and its age, why the company should answer as plain lines of
 * text, and the profile judge's one-line reason and risk. It is open text on the page, not a box: a hairline above it
 * and space around it do the separating. Every line is the API's.
 */
export function WhyToday({ q }: { q: CompanyQueue }) {
  const lines = reasons(q);
  return (
    <section aria-label="Why today" className="grid gap-4 border-t pt-5">
      <div className="grid gap-2">
        <h3 className="text-xs font-medium text-muted-foreground">Why today</h3>
        {q.opening ? <OpeningLine opening={q.opening} /> : <p className="text-[15px] leading-6 text-muted-foreground">No open role at your level. {q.whyToday}</p>}
      </div>
      {lines.length > 0 ? (
        <ul aria-label="Why it should answer" className="flex flex-wrap gap-x-6 gap-y-1.5 text-[13px] leading-5 text-muted-foreground">
          {lines.map((c) => (
            <Reason key={c.text} tone={c.tone}>
              {c.text}
            </Reason>
          ))}
        </ul>
      ) : null}
      <Verdict q={q} />
    </section>
  );
}

/** The three research links: Careers (O), the company's website, its LinkedIn page (a company search; never fetched). */
export function ResearchLinks({ q, keys = true, variant = 'outline' }: { q: CompanyQueue; keys?: boolean; variant?: 'outline' | 'secondary' | 'links' }) {
  const careers = safeHref(q.careersUrl);
  const site = safeHref(q.websiteUrl);
  const linkedin = safeHref(q.linkedinCompanyUrl);
  const items = [
    { href: careers, Icon: Briefcase, label: 'Careers', key: keys ? 'O' : null },
    { href: site, Icon: Globe, label: 'Website', key: null },
    { href: linkedin, Icon: Building, label: 'LinkedIn page', key: null },
  ].flatMap((i) => (i.href ? [{ ...i, href: i.href }] : []));
  // As links they sit on one quiet line under the main actions: the page leads with one button, not six.
  if (variant === 'links') {
    return (
      <div role="group" aria-label="Research" className="flex flex-wrap items-center gap-x-5 gap-y-1 text-[13px] text-muted-foreground">
        {items.map(({ href, Icon, label, key }) => (
          <a
            key={label}
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            aria-keyshortcuts={key?.toLowerCase()}
            className="inline-flex items-center gap-1.5 rounded-sm py-1 outline-none transition-colors duration-150 hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/70"
          >
            <Icon aria-hidden className="size-3.5" />
            {label}
            {key ? <KeyHint k={key} /> : null}
          </a>
        ))}
      </div>
    );
  }
  return (
    <div role="group" aria-label="Research" className="flex flex-wrap items-center gap-2">
      {items.map(({ href, Icon, label, key }) => (
        <Button key={label} asChild variant={variant} size="sm" className={MAIN}>
          <a href={href} target="_blank" rel="noopener noreferrer" aria-keyshortcuts={key?.toLowerCase()}>
            <Icon aria-hidden />
            {label}
            {key ? <KeyHint k={key} /> : null}
          </a>
        </Button>
      ))}
    </div>
  );
}
