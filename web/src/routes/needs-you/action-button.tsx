import { Button } from '@/components/ui/button';
import { Kbd } from '@/components/ui/kbd';

/** One action on a Needs you row: its words, then its key when it has one (a click-only action has none). */
export function ActionButton({
  label,
  keyName,
  variant,
  title,
  onClick,
}: {
  label: string;
  keyName?: string;
  /** `primary` is the one fix on a row, `quiet` a plain text button, `ghost` the bordered quiet one. */
  variant: 'primary' | 'outline' | 'ghost' | 'quiet';
  /** What the button does in the app's older words, on hover. */
  title?: string;
  onClick: () => void;
}) {
  return (
    <Button
      type="button"
      size="sm"
      variant={variant === 'primary' ? 'default' : variant === 'quiet' ? 'ghost' : variant}
      className={variant === 'ghost' ? 'border-border' : variant === 'quiet' ? 'text-muted-foreground hover:text-foreground' : undefined}
      title={title}
      aria-keyshortcuts={keyName?.toLowerCase()}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
    >
      {label}
      {keyName ? (
        <Kbd aria-hidden className={variant === 'primary' ? 'bg-primary-foreground/15 text-primary-foreground' : undefined}>
          {keyName}
        </Kbd>
      ) : null}
    </Button>
  );
}
