import type { PassReason } from '@/api';
import { Button } from '@/components/ui/button';
import { Kbd } from '@/components/ui/kbd';
import { useHotkeys, type Binding } from '@/keyboard/hotkeys';
import { PASS_REASONS } from './picks';

/**
 * Not now's reason chips (top picks, 2026-10-26), in the panel under the pick: keys 1 to 8 pick a reason and pass at
 * once; Enter or Esc pass with no reason (his ruling: the reason is optional). Cancel puts it away with nothing
 * done. A pass is a 14 day cooldown, never a no-go: the copy says so.
 */
export function PassChips({ company, onPass, onCancel }: { company: string; onPass: (reason: PassReason | null) => void; onCancel: () => void }) {
  const group = 'Not now';
  const bindings: Binding[] = [
    ...PASS_REASONS.map((r) => ({ keys: r.key, label: `Not now: ${r.label}`, group, run: () => onPass(r.reason) })),
    { keys: 'enter', label: 'Not now, no reason', group, run: () => onPass(null) },
    { keys: 'escape', label: 'Not now, no reason', group, run: () => onPass(null) },
  ];
  useHotkeys(bindings);
  return (
    <div role="group" aria-label={`Not now for ${company}`} className="grid gap-2.5 rounded-lg border bg-subtle px-4 py-3">
      <p className="text-[13px]">
        <b className="font-medium">{`Not now for ${company}. Why?`}</b>{' '}
        <span className="text-muted-foreground">Optional. It comes back in 14 days, or sooner with a new opening. It is not a no-go.</span>
      </p>
      <div className="flex flex-wrap gap-1.5">
        {PASS_REASONS.map((r) => (
          <Button key={r.reason} type="button" variant="outline" size="sm" className="h-7 gap-1.5 px-2 text-xs" aria-keyshortcuts={r.key} onClick={() => onPass(r.reason)}>
            <Kbd aria-hidden>{r.key}</Kbd>
            {r.label}
          </Button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" variant="ghost" size="sm" className="h-7 gap-1.5 px-2.5 text-xs" aria-keyshortcuts="enter" onClick={() => onPass(null)}>
          No reason
          <Kbd aria-hidden>Enter</Kbd>
        </Button>
        <Button type="button" variant="ghost" size="sm" className="h-7 px-2.5 text-xs" onClick={onCancel}>
          Keep it
        </Button>
      </div>
    </div>
  );
}
