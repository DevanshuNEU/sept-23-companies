import { cn } from '@/lib/utils';

/**
 * The win check (design 16.4): a green stroke that draws once, 240 ms after a 120 ms wait (`win-draw` in
 * globals.css). It is decoration beside the win's own words, so screen readers skip it. With reduced motion
 * the global rule turns the draw off and the check shows complete. `className` sizes it for the larger moments.
 */
export function WinMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={cn('size-6 shrink-0 fill-none stroke-ok stroke-2', className)}
    >
      <path className="win-draw" d="M5 12.5l4.5 4.5L19 7.5" />
    </svg>
  );
}
