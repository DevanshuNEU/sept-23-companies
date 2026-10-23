import type { paths } from '../schema';
import type { Transport, WriteMethod } from './transport';

type Method = Lowercase<WriteMethod> | 'get';

/** The paths of the generated schema that have an operation for `M`. */
export type PathsWith<M extends Method> = { [P in keyof paths]: paths[P][M] extends object ? P : never }[keyof paths];
export type GetPath = PathsWith<'get'>;

/** The JSON body of a 200 answer. A hand type assigned to it fails `pnpm typecheck` when the API's model drifts. */
export type Ok<P extends keyof paths, M extends Method> = paths[P][M] extends { responses: { 200: { content: { 'application/json': infer R } } } } ? R : never;

/** What `{name}` in a path and the query string are filled from. */
export interface Fill {
  params?: Record<string, string | number>;
  query?: Record<string, string | number | undefined>;
}

function url(path: string, { params = {}, query = {} }: Fill): string {
  const filled = path.replace(/\{(\w+)\}/g, (_m, name: string) => encodeURIComponent(String(params[name] ?? '')));
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) if (v !== undefined) qs.set(k, String(v));
  return qs.size > 0 ? `${filled}?${qs}` : filled;
}

export function getJson<P extends GetPath>(t: Transport, path: P, fill: Fill & { signal?: AbortSignal } = {}): Promise<Ok<P, 'get'>> {
  return t.get<Ok<P, 'get'>>(url(path, fill), fill.signal);
}

export function sendJson<M extends Lowercase<WriteMethod>, P extends PathsWith<M>>(
  t: Transport,
  method: M,
  path: P,
  body: unknown,
  o: Parameters<Transport['send']>[3],
  fill: Fill = {},
): Promise<Ok<P, M>> {
  return t.send<Ok<P, M>>(method.toUpperCase() as WriteMethod, url(path, fill), body, o);
}
