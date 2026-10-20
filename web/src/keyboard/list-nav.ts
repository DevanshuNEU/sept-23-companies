import { useRef } from 'react';
import { useSearchParams } from 'react-router';
import { useAnnounce, useHotkeys } from './hotkeys';

export type ListNavOptions<T extends string | number> = {
  ids: T[];
  /** The search param that holds the selection. */
  param?: string;
  onOpen?: (id: T) => void;
  /** Text for the polite live region when the selection changes. */
  announce?: (id: T) => string;
};

export type RowProps = {
  'aria-selected': boolean;
  'data-active': boolean;
  onClick: () => void;
  ref: (el: HTMLElement | null) => void;
};

/**
 * j/k and the arrow keys move the selection without wrapping, Enter opens it. The selection lives in the URL
 * (`?sel=` by default) and is written with replace, so moving through a list adds no history entries.
 */
export function useListNav<T extends string | number>({
  ids,
  param = 'sel',
  onOpen,
  announce,
}: ListNavOptions<T>): { selected: T | null; select: (id: T) => void; rowProps: (id: T) => RowProps } {
  const [params, setParams] = useSearchParams();
  const say = useAnnounce();
  const rows = useRef(new Map<T, HTMLElement>());

  const raw = params.get(param);
  const selected = ids.find((id) => String(id) === raw) ?? null;

  const select = (id: T) => {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set(param, String(id));
        return next;
      },
      { replace: true },
    );
    if (announce) say(announce(id));
  };

  const move = (step: 1 | -1) => {
    if (ids.length === 0) return;
    const at = selected === null ? -1 : ids.indexOf(selected);
    const index = at === -1 ? 0 : Math.min(Math.max(at + step, 0), ids.length - 1);
    const id = ids[index] as T;
    if (id === selected) return;
    select(id);
    rows.current.get(id)?.scrollIntoView({ block: 'nearest' });
  };

  const group = 'Everywhere';
  useHotkeys([
    { keys: 'j', label: 'Next row', group, run: () => move(1) },
    { keys: 'arrowdown', label: 'Next row', group, run: () => move(1) },
    { keys: 'k', label: 'Previous row', group, run: () => move(-1) },
    { keys: 'arrowup', label: 'Previous row', group, run: () => move(-1) },
    {
      keys: 'enter',
      label: 'Open the row',
      group,
      enabled: onOpen !== undefined && selected !== null,
      run: () => {
        if (selected !== null) onOpen?.(selected);
      },
    },
  ]);

  const rowProps = (id: T): RowProps => ({
    'aria-selected': id === selected,
    'data-active': id === selected,
    onClick: () => select(id),
    ref: (el) => {
      if (el) rows.current.set(id, el);
      else rows.current.delete(id);
    },
  });

  return { selected, select, rowProps };
}
