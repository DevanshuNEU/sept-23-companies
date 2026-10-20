import type { IsoTime } from '@/api';
import { Badge } from '@/components/ui/badge';
import { formatDay, formatLocalTime } from '@/lib/format';
import type { Tone } from '@/lib/status';
import { cn } from '@/lib/utils';

const DOT: Record<Tone, string> = {
  ok: 'bg-ok-dot',
  info: 'bg-info-dot',
  warn: 'bg-warn-dot',
  bad: 'bg-bad-dot',
  idle: 'bg-idle-dot',
};

type TimelineItem = { id: string | number; at: IsoTime; text: string; tone: Tone; tag?: string };

/** Newest first as given. A tone dot, the text, an optional source tag, and the day and time in the app's zone. */
export function Timeline({ items }: { items: TimelineItem[] }) {
  return (
    <ol className="grid">
      {items.map((item) => (
        <li
          key={item.id}
          className={cn(
            'relative grid grid-cols-[20px_1fr_auto] gap-2.5 py-1.5 text-[13px]',
            'before:absolute before:top-[22px] before:-bottom-1.5 before:left-[9px] before:w-px before:bg-border last:before:hidden',
          )}
        >
          <span aria-hidden data-tone={item.tone} className={cn('mt-1.5 ml-[5px] size-2 rounded-full', DOT[item.tone])} />
          <span>
            {item.text}
            {item.tag ? (
              <Badge variant="outline" className="ml-1.5 align-middle text-muted-foreground">
                {item.tag}
              </Badge>
            ) : null}
          </span>
          <span className="text-xs text-muted-foreground tabular-nums">
            {`${formatDay(item.at)}, ${formatLocalTime(item.at)}`}
          </span>
        </li>
      ))}
    </ol>
  );
}
