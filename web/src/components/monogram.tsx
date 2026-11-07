import { cn } from '@/lib/utils';

/**
 * The company's first letter on a quiet neutral tile. No hue of its own (2026-11-05, his ruling: no colour for decoration):
 * the list reads as one calm column, and colour is kept for status. Decorative: the name is always written next to it.
 */
export function Monogram({ name, className }: { name: string; className?: string }) {
  const letter = name.trim().charAt(0).toUpperCase() || '?';
  return (
    <span
      aria-hidden
      className={cn('grid size-8 flex-none place-items-center rounded-lg border bg-muted/60 text-sm leading-none font-semibold text-foreground/85 select-none', className)}
    >
      {letter}
    </span>
  );
}
