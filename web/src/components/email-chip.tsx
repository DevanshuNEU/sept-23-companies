import type { EmailFitView } from '@/api';
import { Mail } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

/** The fit's reasons as one sentence run, for a hover and a screen reader. */
const fitWords = (fit: EmailFitView) => `${fit.label}: ${fit.reasons.join('. ')}.`;

/**
 * The email flag (2026-10-27): a small "Email" chip where a cold email is worth it, else "LinkedIn only" in muted text.
 * Calm on purpose: no colour of its own. The reasons show on hover (`title` in the rail, where the row is the control),
 * or in a tooltip when `interactive` (the chip is then a focusable button, so a keyboard reaches the reasons too).
 */
export function EmailChip({ fit, interactive = false, id, className }: { fit: EmailFitView; interactive?: boolean; id?: string; className?: string }) {
  const words = fitWords(fit);
  const chip =
    fit.verdict === 'email' ? (
      <span
        id={id}
        data-email-fit="email"
        title={interactive ? undefined : words}
        className={cn('inline-flex h-5 flex-none items-center gap-1 rounded-full border bg-subtle px-2 text-xs leading-none font-medium whitespace-nowrap text-foreground', className)}
      >
        <Mail aria-hidden className="size-3 text-muted-foreground" />
        Email
      </span>
    ) : (
      <span id={id} data-email-fit="linkedin_only" title={interactive ? undefined : words} className={cn('flex-none text-xs leading-5 whitespace-nowrap text-muted-foreground', className)}>
        LinkedIn only
      </span>
    );
  if (!interactive) return chip;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button type="button" aria-label={words} className="rounded-full outline-none focus-visible:ring-[3px] focus-visible:ring-ring/70">
          {chip}
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-72">
        <ul className="grid gap-0.5">
          {fit.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      </TooltipContent>
    </Tooltip>
  );
}
