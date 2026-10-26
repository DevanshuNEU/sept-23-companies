import { Button } from '@/components/ui/button';
import { Kbd } from '@/components/ui/kbd';

/** One action on a Needs you row: its words, then its key when it has one (a click-only action has none). */
export function ActionButton({
  label,
  keyName,
  variant,
  onClick,
}: {
  label: string;
  keyName?: string;
  variant: 'outline' | 'ghost';
  onClick: () => void;
}) {
  return (
    <Button
      type="button"
      size="sm"
      variant={variant}
      className={variant === 'ghost' ? 'border-border' : undefined}
      aria-keyshortcuts={keyName?.toLowerCase()}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
    >
      {label}
      {keyName ? <Kbd aria-hidden>{keyName}</Kbd> : null}
    </Button>
  );
}
