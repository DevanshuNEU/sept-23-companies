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
  Same<T.Fact, S['Fact']>,
  Same<T.PostingFlag, S['PostingFlag']>,
  Same<T.PostingRow, S['PostingRow']>,
  Same<T.CompanyPerson, S['CompanyPerson']>,
  Same<T.CompanyEvent, S['CompanyEvent']>,
  Same<T.ReviewItem, S['ReviewItem']>,
  Same<T.CompanyView, S['CompanyView']>,
  Same<T.SearchCompany, S['SearchCompany']>,
  Same<T.CompanyHit, S['SearchCompany']>,
  Same<T.SearchPerson, S['SearchPerson']>,
  Same<T.SearchIndex, S['SearchIndex']>,
  Same<T.PostingHit, S['PostingHit']>,
] = [true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true];
