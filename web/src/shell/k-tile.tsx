import { useState } from 'react';
import { cn } from '@/lib/utils';

// The tile knocks twice, once, on the first load of the page. A later mount (a route change, a remount) stays still.
let knocked = false;

/** For tests only: lets the next mount knock again. */
// eslint-disable-next-line react-refresh/only-export-components
export function resetKnock(): void {
  knocked = false;
}

/** The K tile in the brand colour. The knock is a two-tap scale, and is off under reduced motion. */
export function KTile({ className }: { className?: string }) {
  const [knock] = useState(() => {
    const first = !knocked;
    knocked = true;
    return first;
  });
  return (
    <span
      aria-hidden
      className={cn(
        'grid size-6 place-items-center rounded-md bg-brand text-xs font-semibold text-brand-foreground',
        knock && 'knock',
        className,
      )}
    >
      K
    </span>
  );
}
