import type { IsoTime, LinkedInStep } from '@/api';
import { ageDays, formatDay } from '@/lib/format';
import { cn } from '@/lib/utils';

const ORDER: LinkedInStep[] = ['request_sent', 'accepted', 'message_sent', 'replied'];
const WORD: Record<LinkedInStep, string> = {
  request_sent: 'Requested',
  accepted: 'Accepted',
  message_sent: 'Messaged',
  replied: 'Replied',
};

/**
 * Four dots joined by lines, lit up to the latest step, with that step's word and day beside them. `className` and
 * `labelClassName` let a narrow table put the words under the dots.
 */
export function LinkedInSteps({
  steps,
  asOf,
  className,
  labelClassName,
}: {
  steps: { step: LinkedInStep; at: IsoTime }[];
  asOf: IsoTime;
  className?: string;
  labelClassName?: string;
}) {
  let reached = -1;
  let latest: { step: LinkedInStep; at: IsoTime } | null = null;
  for (const s of steps) {
    const i = ORDER.indexOf(s.step);
    if (i > reached) {
      reached = i;
      latest = s;
    }
  }
  const label = latest ? `${WORD[latest.step]} ${ageDays(latest.at, asOf) === 0 ? 'today' : formatDay(latest.at)}` : 'Not started';
  return (
    <span className={cn('inline-flex items-center align-middle', className)}>
      <span role="img" aria-label={label} className="inline-flex items-center">
        {ORDER.map((step, i) => (
          <span key={step} className="inline-flex items-center">
            {i > 0 ? <span data-on={i <= reached} className="h-[1.5px] w-3.5 bg-border data-[on=true]:bg-foreground" /> : null}
            <span
              data-on={i <= reached}
              className={cn(
                'size-2 rounded-full border-[1.5px] border-muted-foreground bg-background',
                'data-[on=true]:border-foreground data-[on=true]:bg-foreground',
                step === 'replied' && 'data-[on=true]:border-ok-dot data-[on=true]:bg-ok-dot',
              )}
            />
          </span>
        ))}
      </span>
      <span className={cn('ml-2 text-xs whitespace-nowrap text-muted-foreground', labelClassName)}>{label}</span>
    </span>
  );
}
