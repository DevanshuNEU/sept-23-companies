import { useId, useState } from 'react';
import { Link } from 'react-router';
import { toast } from 'sonner';
import { ChevronRight, Globe, Terminal } from 'lucide-react';
import type { BenchCompany } from '@/api';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { safeHref } from '@/lib/safe-href';
import { cn } from '@/lib/utils';

async function copyLine(line: string) {
  try {
    await navigator.clipboard.writeText(line);
    toast('Copied the line for your terminal.');
  } catch {
    toast.error('Copy failed. Nothing is on the clipboard. Try again, or select the text and copy it.');
  }
}

function LaneRow({ b }: { b: BenchCompany }) {
  const q = b.queue;
  const site = safeHref(q.websiteUrl);
  return (
    <li data-email-lane={b.company.id} className="grid gap-1 border-b py-2.5 text-[13px]">
      <div className="flex min-w-0 items-baseline gap-3">
        <Link
          to={`/companies/${b.company.id}`}
          className="truncate rounded-sm font-medium underline decoration-foreground/30 underline-offset-3 outline-none hover:decoration-foreground focus-visible:ring-[3px] focus-visible:ring-ring/70"
        >
          {b.company.name}
        </Link>
        <span className="min-w-0 flex-1 truncate text-muted-foreground">{q.whyToday}</span>
        {site ? (
          <a
            href={site}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex flex-none items-center gap-1 rounded-sm text-muted-foreground outline-none hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/70"
          >
            <Globe aria-hidden className="size-3.5" />
            {q.emailFit.website.text}
          </a>
        ) : null}
        <Button type="button" variant="ghost" size="sm" className="h-6 flex-none gap-1 px-2 text-xs" aria-label={`Copy the line for ${b.company.name}`} onClick={() => void copyLine(q.terminalLine)}>
          <Terminal aria-hidden />
          Copy
        </Button>
      </div>
      <p className="text-muted-foreground">{q.emailFit.reasons.join('. ')}</p>
    </li>
  );
}

/**
 * Email today (2026-10-27): up to 6 more companies a day, beyond today's picks, that can be emailed, by rank. Each
 * shows why, its website and the line his terminal reads; he finds 1 or 2 people there and brings the names. Collapsed
 * by default under the plan. Nothing here drafts or sends: the Outbox and his click still do that.
 */
export function EmailLaneList({ lane, text }: { lane: BenchCompany[]; text: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  if (!text) return null;
  return (
    <Collapsible open={open} onOpenChange={setOpen} asChild>
      <section aria-labelledby={id} className="grid gap-2 border-t pt-4 first:border-t-0 first:pt-0">
        <h3 id={id} className="justify-self-start text-sm font-semibold">
          <CollapsibleTrigger className="flex items-center gap-2 rounded-sm text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/70">
            <ChevronRight aria-hidden className={cn('size-4 text-muted-foreground transition-transform', open && 'rotate-90')} />
            Email today
            <span className="text-[13px] font-normal text-muted-foreground tabular-nums">{`${lane.length} ${lane.length === 1 ? 'company' : 'companies'}`}</span>
          </CollapsibleTrigger>
        </h3>
        <CollapsibleContent className="grid gap-2">
          <p className="text-[13px] text-muted-foreground">{text}</p>
          {lane.length > 0 ? (
            <ul aria-label="Companies to email today" className="grid border-t">
              {lane.map((b) => (
                <LaneRow key={b.company.id} b={b} />
              ))}
            </ul>
          ) : null}
        </CollapsibleContent>
      </section>
    </Collapsible>
  );
}
