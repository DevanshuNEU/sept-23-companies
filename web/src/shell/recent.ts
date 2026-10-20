import { useSyncExternalStore } from 'react';
import type { Id } from '@/api';

// The last company pages opened: a UI preference kept in the browser, not data (spec 13.1).
const STORAGE_KEY = 'outreach.recent';
const KEEP = 3;

const listeners = new Set<() => void>();
let cached: { raw: string | null; ids: Id[] } = { raw: null, ids: [] };

function readRaw(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function parse(raw: string | null): Id[] {
  if (raw === null) return [];
  try {
    const value: unknown = JSON.parse(raw);
    return Array.isArray(value) ? value.filter((v): v is Id => Number.isInteger(v)).slice(0, KEEP) : [];
  } catch {
    return [];
  }
}

/** The same array until the stored value changes, as useSyncExternalStore needs. */
function snapshot(): Id[] {
  const raw = readRaw();
  if (raw !== cached.raw) cached = { raw, ids: parse(raw) };
  return cached.ids;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Puts `id` first, drops a repeat, keeps three. */
export function recordRecentCompany(id: Id): void {
  const ids = [id, ...snapshot().filter((x) => x !== id)].slice(0, KEEP);
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
  } catch {
    return; // Blocked storage: the list just does not change.
  }
  for (const l of listeners) l();
}

/** The last three company pages opened, newest first. */
export function useRecentCompanies(): Id[] {
  return useSyncExternalStore(subscribe, snapshot);
}
