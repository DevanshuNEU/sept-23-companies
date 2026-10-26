import { useId, useState } from 'react';
import { Link } from 'react-router';
import { ChevronRight } from 'lucide-react';
import type { InFlightCompany } from '@/api';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { cn } from '@/lib/utils';

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/**
 * In flight, under the queue (company first, #9 2026-10-26): every company with a wave out in the last 14 days or a
 * reply, as the API lists them (replies first, then by the day each is free again), each with where it stands.
 * Collapsed by default: it is the record, not today's work. Nothing when nothing is in flight.
 */
export function InFlightList({ items }: { items: InFlightCompany[] }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  if (items.length === 0) return null;
  const replied = items.filter((f) => f.status === 'replied').length;
  const summary = [plural(items.length, 'company', 'companies'), ...(replied > 0 ? [plural(replied, 'reply', 'replies')] : [])].join(', ');
  return (
    <Collapsible open={open} onOpenChange={setOpen} asChild>
      <section aria-labelledby={id} className="grid gap-2 border-t pt-4 first:border-t-0 first:pt-0">
        <h3 id={id} className="justify-self-start text-sm font-semibold">
          <CollapsibleTrigger className="flex items-center gap-2 rounded-sm text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/70">
            <ChevronRight aria-hidden className={cn('size-4 text-muted-foreground transition-transform', open && 'rotate-90')} />
            In flight
            <span className="text-[13px] font-normal text-muted-foreground tabular-nums">{summary}</span>
          </CollapsibleTrigger>
        </h3>
        <CollapsibleContent>
          <ul aria-label="Companies in flight" className="grid border-t">
            {items.map((f) => (
              <li key={f.company.id} className="grid grid-cols-[minmax(0,180px)_minmax(0,1fr)] items-baseline gap-4 border-b py-2 text-[13px] max-[700px]:grid-cols-1 max-[700px]:gap-0.5">
                <Link
                  to={`/companies/${f.company.id}`}
                  className="truncate rounded-sm font-medium underline decoration-foreground/30 underline-offset-3 outline-none hover:decoration-foreground focus-visible:ring-[3px] focus-visible:ring-ring/70"
                >
                  {f.company.name}
                </Link>
                <span className={cn('min-w-0', f.status === 'replied' ? 'text-info' : 'text-muted-foreground')}>{f.text}</span>
              </li>
            ))}
          </ul>
        </CollapsibleContent>
      </section>
    </Collapsible>
  );
}
