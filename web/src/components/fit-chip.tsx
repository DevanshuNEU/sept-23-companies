import { fitBand, type FitBand } from '@/lib/fit';
import { cn } from '@/lib/utils';

const BAND: Record<FitBand, string> = {
  good: 'border-ok-bd bg-ok-bg text-ok',
  mid: 'border-warn-bd bg-warn-bg text-warn',
  low: 'border-transparent bg-muted text-muted-foreground',
};

/** "Fit 66", only when the judge has read the company (`fit` null draws nothing). */
export function FitChip({ fit, className }: { fit: number | null | undefined; className?: string }) {
  if (fit === null || fit === undefined) return null;
  const band = fitBand(fit);
  return (
    <span
      data-judge
      data-band={band}
      className={cn('inline-flex h-5 items-center rounded-full border px-2 text-xs leading-none font-medium whitespace-nowrap tabular-nums', BAND[band], className)}
    >
      {`Fit ${fit}`}
    </span>
  );
}
