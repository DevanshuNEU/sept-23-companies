import type { DoneView } from '@/api';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { parseIsoDate } from '@/lib/format';
import { cn } from '@/lib/utils';

type Week = DoneView['metrics']['repliesPerWeek'][number];

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "Nov 2" for a calendar date. A date is a calendar date: no zone shifts it. */
function monthDay(on: string): string {
  const { m, d } = parseIsoDate(on);
  return `${MONTHS[m - 1] ?? ''} ${d}`;
}

/**
 * Replies per week, one monochrome series. The API sends the last 8 weeks oldest first, so the last
 * week is the current one: it is drawn in solid foreground and reads "so far".
 */
export function RepliesChart({ weeks }: { weeks: Week[] }) {
  const max = Math.max(0, ...weeks.map((w) => w.count));
  const last = weeks.length - 1;
  const counts = weeks.map((w) => w.count).join(', ');
  return (
    <div
      role="img"
      aria-label={`Replies per week, last ${weeks.length} weeks: ${counts}${weeks.length > 0 ? ' so far' : ''}`}
      className="flex h-8 items-end gap-[3px]"
    >
      {weeks.map((w, i) => {
        const current = i === last;
        const tip = current ? `This week so far: ${w.count}` : `Week of ${monthDay(w.weekOf)}: ${w.count}`;
        return (
          <Tooltip key={w.weekOf}>
            <TooltipTrigger asChild>
              <span
                data-slot="reply-bar"
                data-current={current}
                style={{ height: w.count > 0 && max > 0 ? `${Math.round((w.count / max) * 100)}%` : '2px' }}
                className={cn(
                  'min-h-0.5 flex-1 rounded-t-[2px] transition-opacity hover:opacity-70',
                  current ? 'bg-foreground' : 'bg-foreground/[0.22]',
                )}
              />
            </TooltipTrigger>
            <TooltipContent side="top" sideOffset={6}>
              {tip}
            </TooltipContent>
          </Tooltip>
        );
      })}
    </div>
  );
}
