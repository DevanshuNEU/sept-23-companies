import type { Confidence } from '@/api';
import { cn } from '@/lib/utils';

const CONFIDENCE: Record<Confidence, { bars: 1 | 2 | 3; label: string }> = {
  confirmed: { bars: 3, label: 'High confidence' },
  estimate: { bars: 2, label: 'Medium confidence' },
  guess: { bars: 1, label: 'Guess' },
};

/** Three bars of rising height, filled from the left. The label carries the meaning, the bars repeat it. */
export function ConfidenceMeter({ confidence }: { confidence: Confidence }) {
  const { bars, label } = CONFIDENCE[confidence];
  return (
    <span role="img" aria-label={label} className="inline-flex h-3 items-end gap-0.5 align-middle">
      {[1, 2, 3].map((n) => (
        <span
          key={n}
          data-on={n <= bars}
          className={cn('w-[3px] rounded-[1px] bg-muted-foreground/25 data-[on=true]:bg-foreground', n === 1 ? 'h-1' : n === 2 ? 'h-2' : 'h-3')}
        />
      ))}
    </span>
  );
}
