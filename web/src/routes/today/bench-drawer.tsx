import { Link } from 'react-router';
import { Plus } from 'lucide-react';
import type { BenchCompany, BenchView } from '@/api';
import { EmailChip } from '@/components/email-chip';
import { Button } from '@/components/ui/button';
import { Kbd } from '@/components/ui/kbd';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { FreshTag, ResearchLinks } from './why-today';

/** One bench company, in the compact shape of a pick: the new tag, why today, size, visa, the links and Add to today. */
function BenchRow({ b, room, onAdd }: { b: BenchCompany; room: number; onAdd: (b: BenchCompany) => void }) {
  const q = b.queue;
  return (
    <li data-bench={b.company.id} className="grid gap-2 border-b px-4 py-3 last:border-b-0">
      <div className="flex items-start gap-3">
        <div className="grid min-w-0 flex-1 gap-0.5">
          <Link
            to={`/companies/${b.company.id}`}
            className="w-fit truncate rounded-sm font-medium outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/70"
          >
            {b.company.name}
          </Link>
          <FreshTag opening={q.opening} />
          <p className="truncate text-[13px] text-muted-foreground">{q.whyToday}</p>
        </div>
        <EmailChip fit={q.emailFit} className="mt-1" />
        <Button type="button" variant="outline" size="sm" className="h-7 flex-none gap-1.5 px-2.5 text-xs" disabled={room <= 0} onClick={() => onAdd(b)}>
          <Plus aria-hidden />
          Add to today
        </Button>
      </div>
      <dl className="grid gap-0.5 text-[13px]">
        {q.opening ? (
          <div className="flex gap-2">
            <dt className="sr-only">Opening</dt>
            <dd className="min-w-0 truncate">{q.opening.title}</dd>
          </div>
        ) : null}
        <div className="flex gap-2">
          <dt className="sr-only">Why it should answer</dt>
          <dd className="min-w-0 text-muted-foreground">
            {[...q.reasons.filter((r) => r !== q.whyToday).slice(0, 2), ...(q.sizeLine ? [q.sizeLine] : [])].join('. ')}
          </dd>
        </div>
        <div className="flex gap-2">
          <dt className="text-muted-foreground">Visa</dt>
          <dd className="min-w-0">{q.sponsorshipLine}</dd>
        </div>
        {q.opening?.appliedNote ? (
          <div className="flex gap-2">
            <dt className="sr-only">Applied before</dt>
            <dd className="min-w-0 text-muted-foreground">{q.opening.appliedNote}</dd>
          </div>
        ) : null}
      </dl>
      <ResearchLinks q={q} keys={false} />
    </li>
  );
}

/**
 * The bench ("More options", B): the next best companies not on today's list, new early career openings first, then
 * other new openings, then by rank, up to 12. Add to today puts one after the last pick while the week has room. A
 * company he said Not now to, applied to, or has a wave out at is not here; it comes back on its own.
 */
export function BenchDrawer({ open, onOpenChange, bench, onAdd }: { open: boolean; onOpenChange: (open: boolean) => void; bench: BenchView | undefined; onAdd: (b: BenchCompany) => void }) {
  const companies = bench?.companies ?? [];
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-[440px] gap-0 sm:max-w-[440px]">
        <SheetHeader className="border-b">
          <SheetTitle className="flex items-center gap-2">
            More options
            <Kbd aria-hidden>B</Kbd>
          </SheetTitle>
          <SheetDescription>
            {bench ? bench.roomText : 'Loading the next companies.'} New openings come first.
          </SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {bench && companies.length === 0 ? (
            <p className="px-4 py-6 text-sm text-muted-foreground">No other company is free to add right now. Companies come back after their cooldown or with a new opening.</p>
          ) : (
            <ul aria-label="More options" className="grid">
              {companies.map((b) => (
                <BenchRow key={b.company.id} b={b} room={bench?.room ?? 0} onAdd={onAdd} />
              ))}
            </ul>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
