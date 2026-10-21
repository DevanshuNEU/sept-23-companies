import { createContext, useContext, useEffect, useState, useSyncExternalStore, type ReactNode } from 'react';
import type { Id, Revision } from '@/api';

/** What he has typed and not saved, kept per email. `base` is the revision he started from. */
export type Edit = { subject: string; body: string; base: Revision };

/**
 * Unsaved drafts, held above the router so leaving the Outbox (the sidebar, a g chord) does not lose them. Only
 * text that differs from what is saved is kept: the view deletes an entry when he types back to the saved text or saves.
 */
class EditsStore {
  private readonly map = new Map<Id, Edit>();
  private readonly listeners = new Set<() => void>();
  private count = 0;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  /** How many emails have unsaved text. */
  size = (): number => this.count;

  get(id: Id): Edit | undefined {
    return this.map.get(id);
  }

  set(id: Id, edit: Edit): void {
    this.map.set(id, edit);
    this.changed();
  }

  delete(id: Id): void {
    if (this.map.delete(id)) this.changed();
  }

  /** Drops the text of every email the server no longer has as a draft (sent, skipped or approved elsewhere). */
  prune(stateOf: (id: Id) => string | undefined): void {
    let dropped = false;
    for (const id of [...this.map.keys()]) {
      const state = stateOf(id);
      if (state !== undefined && state !== 'drafted') dropped = this.map.delete(id) || dropped;
    }
    if (dropped) this.changed();
  }

  private changed(): void {
    this.count = this.map.size;
    for (const l of this.listeners) l();
  }
}

const EditsContext = createContext<EditsStore | null>(null);

/** Holds the unsaved drafts and asks the browser to confirm before the page closes or reloads while any exist. */
export function EditsProvider({ children }: { children: ReactNode }) {
  const [store] = useState(() => new EditsStore());
  const unsaved = useSyncExternalStore(store.subscribe, store.size);

  useEffect(() => {
    if (unsaved === 0) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      // Older browsers need a value here to show the prompt.
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [unsaved]);

  return <EditsContext.Provider value={store}>{children}</EditsContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useEdits(): EditsStore {
  const store = useContext(EditsContext);
  if (!store) throw new Error('unsaved edits outside EditsProvider');
  return store;
}
