import { Ban, Check, Clock, Pause, Reply, TriangleAlert, type LucideIcon } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import type { Status, Tone } from '@/lib/status';
import { cn } from '@/lib/utils';

const ICONS: Record<NonNullable<Status['icon']>, LucideIcon> = {
  block: Ban,
  warn: TriangleAlert,
  check: Check,
  reply: Reply,
  pause: Pause,
  clock: Clock,
};

const DOT: Record<Tone, string> = {
  ok: 'bg-ok-dot',
  info: 'bg-info-dot',
  warn: 'bg-warn-dot',
  bad: 'bg-bad-dot',
  idle: 'bg-idle-dot',
};

/** The word is always there; the icon or dot is a second signal beside the color. */
export function StatusBadge({ status, dot = false }: { status: Status; dot?: boolean }) {
  const Icon = status.icon ? ICONS[status.icon] : null;
  return (
    <Badge variant={status.tone}>
      {Icon ? (
        <Icon aria-hidden />
      ) : dot ? (
        <span data-slot="status-dot" aria-hidden className={cn('size-1.5 rounded-full', DOT[status.tone])} />
      ) : null}
      {status.label}
    </Badge>
  );
}
