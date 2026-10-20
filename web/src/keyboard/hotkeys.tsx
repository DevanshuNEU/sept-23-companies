import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';

/**
 * One keyboard binding. `keys` is a combo (`'a'`, `'shift+j'`, `'mod+s'`, `'mod+enter'`, `'?'`, `'/'`,
 * `'escape'`, `'arrowdown'`) or a chord of combos separated by a space (`'g t'`). `mod` is Cmd on a Mac and
 * Ctrl elsewhere.
 */
export type Binding = {
  keys: string;
  label: string;
  group: string;
  run: (e: KeyboardEvent) => void;
  /** Single keys only: also run while focus is in a text field. Mod combos and `escape` always do. */
  inInputs?: boolean;
  /** Also run while a dialog is open. Off by default for everything except `escape`, mod combos included. */
  inDialogs?: boolean;
  /** `false`: never runs, still listed by `useHotkeyRegistry`. */
  enabled?: boolean;
};

/** How long a chord waits for its next key. */
const CHORD_MS = 1000;

type Combo = { key: string; mod: boolean; shift: boolean; alt: boolean };
type Pressed = Combo & { other: boolean };
type Entry = { binding: Binding; steps: Combo[] };

const MODIFIER_KEYS = new Set(['shift', 'control', 'meta', 'alt', 'altgraph', 'capslock', 'fn', 'os']);
const KEY_NAMES: Record<string, string> = { ' ': 'space', esc: 'escape', spacebar: 'space' };
const DIALOGS = '[role="dialog"], [role="alertdialog"]';
const PRESSABLE =
  'button, a[href], summary, [role="button"], [role="link"], [role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"]';

function normalizeKey(key: string): string {
  const lower = key.toLowerCase();
  return KEY_NAMES[lower] ?? lower;
}

/** Letters and named keys (enter, escape, arrows) care about Shift; `?`, `/` and digits do not. */
function shiftMatters(key: string): boolean {
  return /^[a-z]$/.test(key) || key.length > 1;
}

const parsed = new Map<string, Combo[]>();

function parseKeys(keys: string): Combo[] {
  const cached = parsed.get(keys);
  if (cached) return cached;
  const steps = keys
    .trim()
    .split(/\s+/)
    .map((step) => {
      const parts = step.toLowerCase().split('+');
      const key = normalizeKey(parts.pop() ?? '');
      const combo: Combo = { key, mod: false, shift: false, alt: false };
      for (const part of parts) {
        if (part === 'mod' || part === 'shift' || part === 'alt') combo[part] = true;
        else throw new Error(`Unknown modifier "${part}" in "${keys}"`);
      }
      return combo;
    });
  parsed.set(keys, steps);
  return steps;
}

function isMac(): boolean {
  const data = (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData;
  const platform = data?.platform || navigator.platform || navigator.userAgent;
  return /mac|iphone|ipad|ipod/i.test(platform);
}

function readPressed(e: KeyboardEvent): Pressed {
  const mac = isMac();
  return {
    key: normalizeKey(e.key),
    mod: mac ? e.metaKey : e.ctrlKey,
    other: mac ? e.ctrlKey : e.metaKey,
    shift: e.shiftKey,
    alt: e.altKey,
  };
}

function comboMatches(combo: Combo, p: Pressed): boolean {
  if (combo.key !== p.key || combo.mod !== p.mod || combo.alt !== p.alt || p.other) return false;
  return shiftMatters(combo.key) ? combo.shift === p.shift : !combo.shift || p.shift;
}

/** True when keys pressed on `t` belong to a text field: input, textarea, select or contenteditable. */
// eslint-disable-next-line react-refresh/only-export-components
export function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof Element)) return false;
  if (t.closest('input, textarea, select')) return true;
  if ((t as HTMLElement).isContentEditable) return true;
  const editable = t.closest('[contenteditable]');
  return editable !== null && editable.getAttribute('contenteditable') !== 'false';
}

type Guard = { typing: boolean; dialog: boolean; pressable: boolean };

function allowed(binding: Binding, combo: Combo, g: Guard): boolean {
  if (binding.enabled === false) return false;
  const single = !combo.mod && combo.key !== 'escape';
  if (g.typing && single && !binding.inInputs) return false;
  // Behind a dialog nothing acts, not even mod+enter (save, then approve), unless the binding opts in.
  if (g.dialog && combo.key !== 'escape' && !binding.inDialogs) return false;
  // Enter or Space on a focused button or link presses it; a binding must not steal that.
  if (g.pressable && !combo.mod && (combo.key === 'enter' || combo.key === 'space')) return false;
  return true;
}

class HotkeyStore {
  private seq = 0;
  private regs = new Map<number, Binding[]>();
  private listeners = new Set<() => void>();
  private signature = '';
  private snapshot: Binding[] = [];

  nextSeq(): number {
    this.seq += 1;
    return this.seq;
  }

  set(seq: number, bindings: Binding[]) {
    this.regs.set(seq, bindings);
    this.changed();
  }

  delete(seq: number) {
    if (this.regs.delete(seq)) this.changed();
  }

  /** Every binding in mount order (earliest first), each with its parsed steps. */
  entries(): Entry[] {
    return [...this.regs.keys()]
      .sort((a, b) => a - b)
      .flatMap((seq) => (this.regs.get(seq) ?? []).map((binding) => ({ binding, steps: parseKeys(binding.keys) })));
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = () => this.snapshot;

  /** Rebuilds the registry only when what it lists changes, so a new `run` each render costs nothing. */
  private changed() {
    const list = this.listed();
    const signature = JSON.stringify(list.map(({ binding: b }) => [b.keys, b.label, b.group, b.inInputs, b.inDialogs, b.enabled]));
    if (signature === this.signature) return;
    this.signature = signature;
    this.snapshot = list.map(({ binding, seq }) => ({
      ...binding,
      run: (e: KeyboardEvent) => this.regs.get(seq)?.find((b) => b.keys === binding.keys)?.run(e),
    }));
    for (const listener of this.listeners) listener();
  }

  /** One binding per `keys`: the one that would run (the last mounted enabled one), else the last mounted. */
  private listed(): { binding: Binding; seq: number }[] {
    const byKeys = new Map<string, { binding: Binding; seq: number }>();
    for (const seq of [...this.regs.keys()].sort((a, b) => a - b)) {
      for (const binding of this.regs.get(seq) ?? []) {
        const held = byKeys.get(binding.keys);
        if (!held || binding.enabled !== false || held.binding.enabled === false) {
          byKeys.set(binding.keys, { binding, seq });
        }
      }
    }
    return [...byKeys.values()];
  }
}

type HotkeysContextValue = { store: HotkeyStore; announce: (text: string) => void };

const HotkeysContext = createContext<HotkeysContextValue | null>(null);

function useHotkeysContext(hook: string): HotkeysContextValue {
  const value = useContext(HotkeysContext);
  if (!value) throw new Error(`${hook} outside HotkeysProvider`);
  return value;
}

/**
 * Owns the one `keydown` listener on `document` and a polite live region for `useAnnounce`.
 * A held key acts once (repeats are ignored). Single keys never act while focus is in a text field, and only
 * `escape` and `inDialogs` bindings act while a dialog is open.
 */
export function HotkeysProvider({ children }: { children: ReactNode }) {
  const [store] = useState(() => new HotkeyStore());
  const [message, setMessage] = useState('');

  useEffect(() => {
    let pending: { steps: Pressed[]; timer: ReturnType<typeof setTimeout> } | null = null;
    const clearPending = () => {
      if (pending) clearTimeout(pending.timer);
      pending = null;
    };

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.repeat || e.isComposing || e.defaultPrevented) return;
      const pressed = readPressed(e);
      if (MODIFIER_KEYS.has(pressed.key)) return;

      const target = e.composedPath()[0] ?? e.target;
      const guard: Guard = {
        typing: isTypingTarget(target),
        dialog: document.querySelector(DIALOGS) !== null,
        pressable: target instanceof Element && target.closest(PRESSABLE) !== null,
      };
      const prefix = pending?.steps ?? [];
      clearPending();

      // Bindings whose steps so far match the keys of the pending chord plus this one.
      const index = prefix.length;
      const live = store
        .entries()
        .filter(
          ({ binding, steps }) =>
            steps.length > index &&
            prefix.every((p, i) => comboMatches(steps[i] as Combo, p)) &&
            comboMatches(steps[index] as Combo, pressed) &&
            allowed(binding, steps[index] as Combo, guard),
        );

      if (live.some(({ steps }) => steps.length > index + 1)) {
        // The start of a chord: wait for the next key, and do not also run a shorter binding.
        e.preventDefault();
        pending = { steps: [...prefix, pressed], timer: setTimeout(clearPending, CHORD_MS) };
        return;
      }
      // Inside a chord, a key that completes nothing just cancels it.
      const winner = live.filter(({ steps }) => steps.length === index + 1).pop();
      if (!winner) return;
      e.preventDefault();
      winner.binding.run(e);
    };

    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      clearPending();
    };
  }, [store]);

  const value = useMemo(() => ({ store, announce: setMessage }), [store]);

  return (
    <HotkeysContext.Provider value={value}>
      {children}
      <div role="status" aria-live="polite" aria-atomic="true" className="sr-only">
        {message}
      </div>
    </HotkeysContext.Provider>
  );
}

/** Registers `bindings` while the calling component is mounted. The last mounted wins a clash. */
// eslint-disable-next-line react-refresh/only-export-components
export function useHotkeys(bindings: Binding[]): void {
  const { store } = useHotkeysContext('useHotkeys');
  // Taken during the first render, so a parent ranks before its children and an earlier page before a later one.
  const [seq] = useState(() => store.nextSeq());
  useLayoutEffect(() => {
    store.set(seq, bindings);
  });
  useLayoutEffect(() => () => store.delete(seq), [store, seq]);
}

/** Every mounted binding, one per key (the one that would run), disabled ones included. */
// eslint-disable-next-line react-refresh/only-export-components
export function useHotkeyRegistry(): Binding[] {
  const { store } = useHotkeysContext('useHotkeyRegistry');
  return useSyncExternalStore(store.subscribe, store.getSnapshot);
}

/** Puts `text` in the provider's polite live region, for screen readers. */
// eslint-disable-next-line react-refresh/only-export-components
export function useAnnounce(): (text: string) => void {
  return useHotkeysContext('useAnnounce').announce;
}
