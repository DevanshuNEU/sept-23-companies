import { TriangleAlert } from 'lucide-react';
import type { SourceLink } from '@/api';
import { safeHref } from '@/lib/safe-href';
import { cn } from '@/lib/utils';

/**
 * One source: its label as a link that opens in a new tab and hands over nothing. A URL that is not http
 * or https is never a link; its label stays as plain text with "No link" beside it, so a fact is never
 * shown as sourced when it is not.
 */
export function SourceAnchor({ link, className }: { link: SourceLink; className?: string }) {
  const href = safeHref(link.url);
  if (!href) {
    return (
      <span className={cn('inline-flex flex-wrap items-center gap-x-1.5', className)} data-unlinked>
        <span>{link.label}</span>
        <span className="inline-flex items-center gap-1 text-warn">
          <TriangleAlert aria-hidden className="size-3" />
          No link
        </span>
      </span>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        'rounded-sm underline decoration-foreground/30 underline-offset-3 transition-colors outline-none hover:decoration-foreground focus-visible:ring-[3px] focus-visible:ring-ring/70',
        className,
      )}
    >
      {link.label}
    </a>
  );
}

/**
 * Where a reason comes from: "From" and each source (design 16.1, `.work-sources`). Nothing with no sources.
 * The caller sets the spacing around it (the mockup puts it 6 px under the reason: `mt-1.5`).
 */
export function SourceLinks({ links, className }: { links: SourceLink[]; className?: string }) {
  if (links.length === 0) return null;
  return (
    <div className={cn('flex flex-wrap items-center gap-x-3.5 gap-y-1 text-xs text-muted-foreground', className)}>
      <span>From</span>
      {links.map((link, i) => (
        <SourceAnchor key={`${i}:${link.url}`} link={link} />
      ))}
    </div>
  );
}
