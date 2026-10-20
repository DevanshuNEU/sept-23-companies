import type { Confidence } from '@/api';

const CONFIDENCE: Record<Confidence, { bars: 1 | 2 | 3; label: string }> = {
  confirmed: { bars: 3, label: 'High confidence' },
  estimate: { bars: 2, label: 'Medium confidence' },
  guess: { bars: 1, label: 'Guess' },
};

/** Three equal bars, 4px by 10px, the first N lit. The label carries the meaning, the bars repeat it. */
export function ConfidenceMeter({ confidence }: { confidence: Confidence }) {
  const { bars, label } = CONFIDENCE[confidence];
  return (
    <span role="img" aria-label={label} className="inline-flex gap-0.5 align-middle">
      {[1, 2, 3].map((n) => (
        <i key={n} data-on={n <= bars} className="h-2.5 w-1 rounded-[1px] bg-muted data-[on=true]:bg-muted-foreground" />
      ))}
    </span>
  );
}
