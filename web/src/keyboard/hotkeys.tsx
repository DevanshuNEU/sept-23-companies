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

/** What a list view has selected: a row id, or null for nothing. */
export type Selection = string | number | null;
/** Bindings registered with a selection act on that row; see `useHotkeys` and `useSelectionSync`. */
type Tag = { value: Selection };
type Reg = { bindings: Binding[]; tag: Tag | undefined };
type Entry = { binding: Binding; steps: Combo[]; tag: Tag | undefined };

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

/** True on a Mac or iOS device, where `mod` is Cmd. */
// eslint-disable-next-line react-refresh/only-export-components
export function isMac(): boolean {
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

/** The most keys that can wait for a selection to commit. A person cannot type faster than a render. */
const MAX_WAITING = 16;

/** A key that arrived while the selection it follows was still committing. */
type Waiting = { prefix: Pressed[]; pressed: Pressed; guard: Guard; event: KeyboardEvent; target: Selection };

/** The bindings for one key press: those whose steps so far match, are allowed, and (unless asked) are enabled. */
function matching(entries: Entry[], prefix: Pressed[], pressed: Pressed, guard: Guard, ignoreEnabled = false): Entry[] {
  const index = prefix.length;
  return entries.filter(
    ({ binding, steps }) =>
      steps.length > index &&
      prefix.every((p, i) => comboMatches(steps[i] as Combo, p)) &&
      comboMatches(steps[index] as Combo, pressed) &&
      allowed(ignoreEnabled ? { ...binding, enabled: undefined } : binding, steps[index] as Combo, guard),
  );
}

/** The binding that ends the key sequence, last mounted first. */
function finishing(found: Entry[], index: number): Entry | undefined {
  return found.filter(({ steps }) => steps.length === index + 1).pop();
}

class HotkeyStore {
  private seq = 0;
  private regs = new Map<number, Reg>();
  private listeners = new Set<() => void>();
  private signature = '';
  private snapshot: Binding[] = [];

  /** The selection the last render committed, or undefined while no view has reported one. */
  private committed: Selection | undefined;
  /** Moves made since then that no render has committed yet, oldest first. */
  private moves: Selection[] = [];
  private waiting: Waiting[] = [];

  nextSeq(): number {
    this.seq += 1;
    return this.seq;
  }

  set(seq: number, bindings: Binding[], tag?: Tag) {
    this.regs.set(seq, { bindings, tag });
    this.changed();
  }

  delete(seq: number) {
    if (this.regs.delete(seq)) this.changed();
  }

  /** Every binding in mount order (earliest first), each with its parsed steps. */
  entries(): Entry[] {
    return [...this.regs.keys()]
      .sort((a, b) => a - b)
      .flatMap((seq) => (this.regs.get(seq)?.bindings ?? []).map((binding) => ({ binding, steps: parseKeys(binding.keys), tag: this.regs.get(seq)?.tag })));
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = () => this.snapshot;

  /* ---------------------------------------------------------------- the selection he sees */

  /** The row he sees selected right now: the last move, committed or not. Null before any view reports. */
  latest(): Selection {
    return this.moves.length > 0 ? (this.moves[this.moves.length - 1] as Selection) : (this.committed ?? null);
  }

  /** True while a move waits for its render. */
  get moving(): boolean {
    return this.moves.length > 0 && this.latest() !== this.committed;
  }

  /** Records a move the moment he makes it, before the URL write has rendered. */
  move(id: Selection) {
    this.moves.push(id);
  }

  /**
   * A render committed with this selection. It settles the moves up to it (a render may skip some). A render
   * of the old selection while a move still waits (the URL write runs as a transition) settles nothing.
   */
  commit(id: Selection) {
    const at = this.moves.lastIndexOf(id);
    if (at !== -1) this.moves = this.moves.slice(at + 1);
    this.committed = id;
    this.settle();
  }

  /** The view that reported selections is gone. */
  release() {
    this.committed = undefined;
    this.moves = [];
    this.waiting = [];
  }

  /** True when `entry` was registered for a row other than the one he sees now. */
  stale(entry: Entry): boolean {
    return entry.tag !== undefined && this.committed !== undefined && entry.tag.value !== this.latest();
  }

  /** Holds a key until the render of the selection it follows has committed. */
  wait(w: Waiting) {
    this.waiting = [...this.waiting, w].slice(-MAX_WAITING);
  }

  /**
   * Runs the keys that waited, once their selection has committed. `commit` runs in a passive effect, after
   * every layout effect of that render has registered its bindings, so the bindings for the new row are in.
   * A key whose selection was skipped, or whose row has no such binding (still loading, or it does not apply),
   * is dropped.
   */
  private settle() {
    if (this.waiting.length === 0) return;
    const queue = this.waiting;
    this.waiting = [];
    const keep: Waiting[] = [];
    for (const w of queue) {
      if (w.target !== this.committed) {
        if (this.moves.includes(w.target)) keep.push(w);
        continue;
      }
      const found = finishing(matching(this.entries(), w.prefix, w.pressed, w.guard), w.prefix.length);
      if (found?.tag?.value === w.target) found.binding.run(w.event);
    }
    this.waiting = [...keep, ...this.waiting];
  }

  /** Rebuilds the registry only when what it lists changes, so a new `run` each render costs nothing. */
  private changed() {
    const list = this.listed();
    const signature = JSON.stringify(list.map(({ binding: b }) => [b.keys, b.label, b.group, b.inInputs, b.inDialogs, b.enabled]));
    if (signature === this.signature) return;
    this.signature = signature;
    this.snapshot = list.map(({ binding, seq }) => ({
      ...binding,
      run: (e: KeyboardEvent) => this.regs.get(seq)?.bindings.find((b) => b.keys === binding.keys)?.run(e),
    }));
    for (const listener of this.listeners) listener();
  }

  /** One binding per `keys`: the one that would run (the last mounted enabled one), else the last mounted. */
  private listed(): { binding: Binding; seq: number }[] {
    const byKeys = new Map<string, { binding: Binding; seq: number }>();
    for (const seq of [...this.regs.keys()].sort((a, b) => a - b)) {
      for (const binding of this.regs.get(seq)?.bindings ?? []) {
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
      const live = matching(store.entries(), prefix, pressed, guard);

      if (live.some(({ steps }) => steps.length > index + 1)) {
        // The start of a chord: wait for the next key, and do not also run a shorter binding.
        e.preventDefault();
        pending = { steps: [...prefix, pressed], timer: setTimeout(clearPending, CHORD_MS) };
        return;
      }
      // Inside a chord, a key that completes nothing just cancels it.
      let winner = finishing(live, index);
      // While a move waits to render, the binding on screen may be switched off for the row he moved to (and on
      // for the one he left). Look for bindings for the new row among all of them, enabled or not.
      if (!winner && store.moving) {
        const any = finishing(matching(store.entries(), prefix, pressed, guard, true), index);
        if (any && store.stale(any)) winner = any;
      }
      if (!winner) return;
      e.preventDefault();
      if (store.stale(winner)) {
        // Registered for the row he just left. Hold the key for the row he moved to; with no move on the way, drop it.
        if (store.moving) store.wait({ prefix, pressed, guard, event: e, target: store.latest() });
        return;
      }
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

/**
 * Registers `bindings` while the calling component is mounted. The last mounted wins a clash.
 *
 * Pass `selection` for bindings that act on the selected row (approve, pick, log): the id of the row this
 * render was built for. A key pressed after he moved to another row, before that move has rendered, waits for
 * the render and then acts on the row he moved to. It never acts on the row he left. Leave `selection` out for
 * keys that do not depend on the row (j, k, tabs, search).
 */
// eslint-disable-next-line react-refresh/only-export-components
export function useHotkeys(bindings: Binding[], opts?: { selection: Selection }): void {
  const { store } = useHotkeysContext('useHotkeys');
  // Taken during the first render, so a parent ranks before its children and an earlier page before a later one.
  const [seq] = useState(() => store.nextSeq());
  const tag = opts ? { value: opts.selection } : undefined;
  useLayoutEffect(() => {
    store.set(seq, bindings, tag);
  });
  useLayoutEffect(() => () => store.delete(seq), [store, seq]);
}

/** The row he sees selected, which can be ahead of the render, and a way to record a move the moment he makes it. */
export type LiveSelection<T extends string | number> = {
  /** The row he moved to last, committed or not; the selection of the last render if he has not moved. */
  latest: () => T | null;
  /** Call before writing the move to the URL. */
  move: (id: T | null) => void;
};

/** `LiveSelection` without reporting a selection, for components under the one view that calls `useSelectionSync`. */
// eslint-disable-next-line react-refresh/only-export-components
export function useLiveSelection<T extends string | number>(): LiveSelection<T> {
  const { store } = useHotkeysContext('useLiveSelection');
  return useMemo(() => ({ latest: () => store.latest() as T | null, move: (id) => store.move(id) }), [store]);
}

/**
 * Call once in a list view with the selection its render shows (read from the URL). The selection lives in the
 * URL and that write renders late, so the view also records each move at once (`move`) and reads the row he
 * moved to (`latest`) when a key arrives. See `useHotkeys`.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function useSelectionSync<T extends string | number>(committed: T | null): LiveSelection<T> {
  const { store } = useHotkeysContext('useSelectionSync');
  // Passive, so it runs after every layout effect of the render has registered its bindings.
  useEffect(() => {
    store.commit(committed);
  });
  useEffect(() => () => store.release(), [store]);
  return useLiveSelection<T>();
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
