import { fitBand, type FitBand } from '@/lib/fit';
import { cn } from '@/lib/utils';

// Quiet text, no pill and no colour: a good fit reads in the main colour, the rest in the muted one. A fit is not a status.
const BAND: Record<FitBand, string> = {
  good: 'text-foreground',
  mid: 'text-muted-foreground',
  low: 'text-muted-foreground',
};

/** "Fit 66", only when the judge has read the company (`fit` null draws nothing). */
export function FitChip({ fit, className }: { fit: number | null | undefined; className?: string }) {
  if (fit === null || fit === undefined) return null;
  const band = fitBand(fit);
  return (
    <span data-judge data-band={band} className={cn('flex-none text-xs leading-5 font-medium whitespace-nowrap tabular-nums', BAND[band], className)}>
      {`Fit ${fit}`}
    </span>
  );
}
