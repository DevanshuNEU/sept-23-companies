import { useState } from 'react';
import type { PassReason } from '@/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Kbd } from '@/components/ui/kbd';
import { useHotkeys, type Binding } from '@/keyboard/hotkeys';
import { NOTE_REASONS, PASS_REASONS } from './picks';

const NOTE_MAX = 280;

/**
 * Not now's reason chips (top picks, 2026-10-26), in the panel under the pick: keys 1 to 8, 9 and 0 pick a reason and
 * pass at once; Enter or Esc pass with no reason (his ruling: the reason is optional). Role not for me and Other first
 * ask for his own words (2026-10-28): Enter passes with them, or with nothing typed, and Esc goes back to the chips.
 * Cancel puts it away with nothing done. A pass is a 14 day cooldown, never a no-go: the copy says so. Wrong company
 * and Wrong location fix the posting or board instead and give no cooldown.
 */
export function PassChips({ company, onPass, onCancel }: { company: string; onPass: (reason: PassReason | null, note?: string) => void; onCancel: () => void }) {
  const group = 'Not now';
  const [asking, setAsking] = useState<PassReason | null>(null);
  const [note, setNote] = useState('');
  const choose = (reason: PassReason) => {
    if (NOTE_REASONS.includes(reason)) setAsking(reason);
    else onPass(reason);
  };
  const bindings: Binding[] = asking
    ? []
    : [
        ...PASS_REASONS.map((r) => ({ keys: r.key, label: `Not now: ${r.label}`, group, run: () => choose(r.reason) })),
        { keys: 'enter', label: 'Not now, no reason', group, run: () => onPass(null) },
        { keys: 'escape', label: 'Not now, no reason', group, run: () => onPass(null) },
      ];
  useHotkeys(bindings);
  const label = PASS_REASONS.find((r) => r.reason === asking)?.label;
  return (
    <div role="group" aria-label={`Not now for ${company}`} className="grid gap-2.5 rounded-lg border bg-subtle px-4 py-3">
      <p className="text-[13px]">
        <b className="font-medium">{`Not now for ${company}. Why?`}</b>{' '}
        <span className="text-muted-foreground">
          Optional. It comes back in 14 days, or sooner with a new opening. It is not a no-go. Wrong company and Wrong location fix the data and are not a Not now.
        </span>
      </p>
      {asking ? (
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            onPass(asking, note.trim() || undefined);
          }}
        >
          <Input
            autoFocus
            aria-label="Why"
            placeholder={`${label}: say what is wrong, in your words (optional)`}
            maxLength={NOTE_MAX}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                e.preventDefault();
                e.stopPropagation();
                setAsking(null);
                setNote('');
              }
            }}
            className="h-8 max-w-xl flex-1 text-[13px]"
          />
          <Button type="submit" size="sm" className="h-7 gap-1.5 px-2.5 text-xs">
            Not now
            <Kbd aria-hidden>Enter</Kbd>
          </Button>
        </form>
      ) : (
        <>
          <div className="flex flex-wrap gap-1.5">
            {PASS_REASONS.map((r) => (
              <Button key={r.reason} type="button" variant="outline" size="sm" className="h-7 gap-1.5 px-2 text-xs" aria-keyshortcuts={r.key} onClick={() => choose(r.reason)}>
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
        </>
      )}
    </div>
  );
}
