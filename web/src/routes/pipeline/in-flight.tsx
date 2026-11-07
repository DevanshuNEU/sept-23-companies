import { Link } from 'react-router';
import type { InFlightCompany } from '@/api';
import { Monogram } from '@/components/monogram';
import { cn } from '@/lib/utils';

/**
 * In flight, the second tab of the Pipeline page (company first, #9 2026-10-26): every company with a wave out in the
 * last 14 days or a reply, as the API lists them (replies first, then by the day each is free again), each with where
 * it stands in the API's words. It is the record, not today's work. Nothing when nothing is in flight.
 */
export function InFlightList({ items }: { items: InFlightCompany[] }) {
  if (items.length === 0) return null;
  return (
    <section aria-label="In flight">
      <ul aria-label="Companies in flight" className="divide-y overflow-hidden rounded-xl border bg-card">
        {items.map((f) => (
          <li key={f.company.id} className="grid grid-cols-[32px_minmax(0,1fr)] items-center gap-x-3 px-4 py-3 transition-colors duration-150 hover:bg-muted/40">
            <Monogram name={f.company.name} />
            <div className="grid min-w-0 gap-0.5">
              <Link
                to={`/companies/${f.company.id}`}
                className="w-fit max-w-full truncate rounded-sm text-sm font-medium outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/70"
              >
                {f.company.name}
              </Link>
              <span className={cn('min-w-0 text-[13px] leading-5', f.status === 'replied' ? 'font-medium text-foreground' : 'text-muted-foreground')}>{f.text}</span>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
