import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

/**
 * The top of a page: the title, one muted line under it, and an optional aside at the right of the title row. Every view
 * uses it, so the title size, the tracking and the gap to the summary are one thing, not seven copies.
 */
export function PageHeader({ title, summary, aside, className }: { title: string; summary?: ReactNode; aside?: ReactNode; className?: string }) {
  return (
    <header className={cn('flex flex-wrap items-start justify-between gap-x-6 gap-y-2', className)}>
      <div className="grid gap-0.5">
        <h1 className="text-xl leading-7 font-semibold tracking-[-0.01em]">{title}</h1>
        {summary ? (
          <div className="grid gap-0.5 text-[13px] leading-5 text-muted-foreground tabular-nums" aria-live="polite">
            {summary}
          </div>
        ) : null}
      </div>
      {aside}
    </header>
  );
}
