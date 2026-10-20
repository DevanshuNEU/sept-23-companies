import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Kbd, KbdGroup } from '@/components/ui/kbd';
import { useHotkeyRegistry, type Binding } from '@/keyboard/hotkeys';

const isMac = () => /mac|iphone|ipad|ipod/i.test(navigator.platform || navigator.userAgent);

const NAMED: Record<string, string> = {
  escape: 'Esc',
  enter: 'Enter',
  space: 'Space',
  arrowdown: '↓',
  arrowup: '↑',
  arrowleft: '←',
  arrowright: '→',
};

/** The keycaps for a binding's `keys`: `mod+k` is `⌘` and `K`, the chord `g t` is `G` and `T`. */
// eslint-disable-next-line react-refresh/only-export-components
export function keyCaps(keys: string): string[] {
  const mac = isMac();
  return keys
    .trim()
    .split(/\s+/)
    .flatMap((step) => {
      const parts = step.split('+');
      const key = (parts.pop() ?? '').toLowerCase();
      const mods = parts.map((m) => {
        if (m === 'mod') return mac ? '⌘' : 'Ctrl';
        if (m === 'shift') return mac ? '⇧' : 'Shift';
        return mac ? '⌥' : 'Alt';
      });
      return [...mods, NAMED[key] ?? (key.length === 1 ? key.toUpperCase() : key)];
    });
}

const EVERYWHERE = 'Everywhere';

/** The registry by group: Everywhere first, then each view's group in the order its keys were mounted. */
function byGroup(bindings: Binding[]): [group: string, rows: Binding[]][] {
  const groups = new Map<string, Binding[]>();
  for (const b of bindings) groups.set(b.group, [...(groups.get(b.group) ?? []), b]);
  const names = [...groups.keys()].sort((a, b) => Number(b === EVERYWHERE) - Number(a === EVERYWHERE));
  return names.map((name) => [name, groups.get(name) ?? []]);
}

/** Every key that works on this page, from what is mounted (spec section 3). Opens with `?`. */
export function ShortcutSheet({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const registry = useHotkeyRegistry();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-[640px]">
        <DialogHeader>
          <DialogTitle className="text-lg">Keyboard shortcuts</DialogTitle>
          <DialogDescription className="sr-only">Every key that works on this page.</DialogDescription>
        </DialogHeader>
        {byGroup(registry).map(([group, rows]) => (
          <section key={group} aria-label={group}>
            <h3 className="mb-1 text-xs font-medium text-muted-foreground">{group}</h3>
            <ul className="grid grid-cols-1 gap-x-6 gap-y-1 text-[13px] sm:grid-cols-2">
              {rows.map((b) => (
                <li key={b.keys} className="flex items-center justify-between gap-3">
                  <span>{b.label}</span>
                  <KbdGroup>
                    {keyCaps(b.keys).map((cap, i) => (
                      <Kbd key={i}>{cap}</Kbd>
                    ))}
                  </KbdGroup>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </DialogContent>
    </Dialog>
  );
}
