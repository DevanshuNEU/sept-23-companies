import { Link } from 'react-router';
import { Globe } from 'lucide-react';
import type { BenchCompany } from '@/api';
import { Monogram } from '@/components/monogram';
import { safeHref } from '@/lib/safe-href';

function LaneRow({ b }: { b: BenchCompany }) {
  const q = b.queue;
  const site = safeHref(q.websiteUrl);
  return (
    <li data-email-lane={b.company.id} className="grid grid-cols-[32px_minmax(0,1fr)_auto] items-center gap-x-3 px-4 py-3 transition-colors duration-150 hover:bg-muted/40">
      <Monogram name={b.company.name} />
      <div className="grid min-w-0 gap-0.5">
        <Link to={`/companies/${b.company.id}`} className="w-fit max-w-full truncate rounded-sm text-sm font-medium outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/70">
          {b.company.name}
        </Link>
        <span className="truncate text-[13px] leading-5 text-muted-foreground">{q.whyToday}</span>
        <p className="text-[13px] leading-5 text-muted-foreground">{q.emailFit.reasons.join('. ')}</p>
      </div>
      {site ? (
        <a
          href={site}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex flex-none items-center gap-1.5 rounded-sm text-[13px] text-muted-foreground outline-none hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/70"
        >
          <Globe aria-hidden className="size-3.5" />
          {q.emailFit.website.text}
        </a>
      ) : null}
    </li>
  );
}

/**
 * Email today (2026-10-27), the third tab of the Pipeline page: up to 6 more companies a day, beyond today's picks, that
 * can be emailed, by rank. Each shows why and its website; he finds 1 or 2 people there and brings the names. Nothing
 * here drafts or sends: the Outbox and his click still do that. The per-company terminal line is gone from this list
 * (2026-11-06: he does not use it).
 */
export function EmailLaneList({ lane, text }: { lane: BenchCompany[]; text: string }) {
  if (!text) return null;
  return (
    <section aria-label="Email today" className="grid gap-3">
      <p className="px-1 text-[13px] text-muted-foreground">{text}</p>
      {lane.length > 0 ? (
        <ul aria-label="Companies to email today" className="divide-y overflow-hidden rounded-xl border bg-card">
          {lane.map((b) => (
            <LaneRow key={b.company.id} b={b} />
          ))}
        </ul>
      ) : null}
    </section>
  );
}
