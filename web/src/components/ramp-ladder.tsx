import { cn } from '@/lib/utils';

type RampLadderProps = { levels: number[]; level: number; sent: number; limit: number; mini?: boolean };

const pct = (n: number) => `${Number(n.toFixed(2))}%`;

/** One bar per ramp level, 40% to 100% tall. The current bar fills by today's sent share of the limit. */
export function RampLadder({ levels, level, sent, limit, mini = false }: RampLadderProps) {
  const last = Math.max(levels.length - 1, 1);
  const fill = limit > 0 ? Math.min(100, Math.round((sent / limit) * 100)) : 0;
  return (
    <span
      role="img"
      aria-label={`Limit level ${level} of ${levels.length}: ${limit} a day. Today ${sent} sent.`}
      data-mini={mini}
      className={cn('inline-flex items-end align-middle', mini ? 'h-3.5 gap-0.5' : 'h-[30px] gap-[3px]')}
    >
      {levels.map((_, i) => {
        const state = i + 1 < level ? 'past' : i + 1 === level ? 'now' : 'next';
        return (
          <span
            key={i}
            data-step={state}
            style={{ height: pct(40 + (60 * i) / last) }}
            className={cn(
              'relative overflow-hidden bg-muted data-[step=past]:bg-foreground/30 data-[step=now]:bg-foreground/[0.16]',
              mini ? 'w-1 rounded-[1px]' : 'w-[7px] rounded-[2px]',
            )}
          >
            {state === 'now' ? <i className="absolute inset-x-0 bottom-0 bg-foreground" style={{ height: pct(fill) }} /> : null}
          </span>
        );
      })}
    </span>
  );
}
