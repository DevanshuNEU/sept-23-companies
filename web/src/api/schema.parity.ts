// The generated types compile against the views: each entry says a name in `types.ts` and the same name in the
// generated `schema.ts` accept exactly the same values. `pnpm typecheck` compiles this file. Add one entry per
// name as its API view lands; a mismatch turns the entry's `true` into a type error.
import type * as T from './types';
import type { components } from './schema';

type S = components['schemas'];

export type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : never) : never;

export const _parity: [
  Same<T.Check, S['Check']>,
  Same<T.JobRunSummary, S['JobRunSummary']>,
  Same<T.ReadQueueStatus, S['ReadQueueStatus']>,
  Same<T.HealthView, S['HealthView']>,
] = [true, true, true, true];
