import { Link } from 'react-router';
import type { HiddenCompany } from '@/api';
import { Badge } from '@/components/ui/badge';
import { hideKindLabel } from '@/lib/status';

const link =
  'rounded-sm font-medium underline decoration-border underline-offset-3 outline-none hover:decoration-current focus-visible:ring-[3px] focus-visible:ring-ring/50';

/**
 * Companies the ranker hides, each with the reason it stored (design 16.9). A hide that waits on the posting reader
 * says "Hidden, pending reader": it can change when the reader has read the posting. Nothing here is a no-go row, so
 * nothing here can be taken off the list; the page only shows what the ranker decided.
 */
export function HiddenCompanies({ items }: { items: HiddenCompany[] }) {
  if (items.length === 0) return null;
  return (
    <section aria-labelledby="hidden-h" className="mt-4 grid gap-2">
      <div>
        <h2 id="hidden-h" className="text-sm font-semibold">Hidden by the ranking</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">The ranker keeps these out of every list. Each shows the reason it stored.</p>
      </div>
      <ul className="overflow-hidden rounded-lg border">
        {items.map((h) => (
          <li key={h.company.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-2.5 text-[13px] last:border-b-0">
            <Link to={`/companies/${h.company.id}`} className={link}>
              {h.company.name}
            </Link>
            <Badge variant="outline">{hideKindLabel[h.kind]}</Badge>
            <span className="min-w-0 text-muted-foreground">{h.reason}</span>
            {h.provisional ? <Badge variant="idle">Hidden, pending reader</Badge> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
