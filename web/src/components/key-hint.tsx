import { Kbd } from '@/components/ui/kbd';
import { cn } from '@/lib/utils';

/** A key hint on a button. On the filled button it is the mockup's translucent chip in the button's own color. */
export function KeyHint({ k, filled = false }: { k: string; filled?: boolean }) {
  return (
    <Kbd aria-hidden className={cn(filled && 'bg-primary-foreground/15 text-primary-foreground')}>
      {k}
    </Kbd>
  );
}
