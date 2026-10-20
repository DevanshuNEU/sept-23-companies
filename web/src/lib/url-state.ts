import { useCallback } from 'react';
import { useSearchParams } from 'react-router';

/**
 * One value in the URL query (selection, tab, filter). The initial value is never written, and
 * setting it removes the param, so a clean URL means the default view. Replaces the history entry
 * unless `push` is set.
 */
export function useUrlState(key: string, initial: string, opts?: { push?: boolean }): [string, (v: string) => void] {
  const [params, setParams] = useSearchParams();
  const push = opts?.push === true;
  const value = params.get(key) ?? initial;
  const set = useCallback(
    (v: string) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (v === initial) next.delete(key);
          else next.set(key, v);
          return next;
        },
        { replace: !push },
      );
    },
    [setParams, key, initial, push],
  );
  return [value, set];
}
