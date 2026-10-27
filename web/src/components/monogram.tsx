import { monogramHue } from '@/lib/monogram';
import { cn } from '@/lib/utils';

/** The company's first letter on its own hue. Decorative: the name is always written next to it. */
export function Monogram({ name, className }: { name: string; className?: string }) {
  const hue = monogramHue(name);
  const letter = name.trim().charAt(0).toUpperCase() || '?';
  return (
    <span
      aria-hidden
      data-hue={hue}
      style={{ backgroundColor: `var(--mono-${hue}-bg)`, color: `var(--mono-${hue}-fg)` }}
      className={cn('grid size-8 flex-none place-items-center rounded-lg text-sm leading-none font-semibold select-none', className)}
    >
      {letter}
    </span>
  );
}
