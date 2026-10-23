// @vitest-environment node
/// <reference types="node" />
// `pnpm test` fails when `src/api/schema.ts` is not what openapi-typescript makes from `openapi.json`. This is the
// same output `pnpm gen:api` writes (the CLI is this call plus the header), so the fix for a failure is
// `uv run python -m outreach.api.openapi --write && cd web && pnpm gen:api`.
import { readFileSync } from 'node:fs';
import openapiTS, { astToString, COMMENT_HEADER } from 'openapi-typescript';
import type { OpenAPI3 } from 'openapi-typescript';
import { describe, expect, it } from 'vitest';

const here = (name: string) => new URL(`../../${name}`, import.meta.url);
const committedSchema = () => readFileSync(new URL('./schema.ts', import.meta.url), 'utf8');
const openapiJson = () => JSON.parse(readFileSync(here('openapi.json'), 'utf8')) as OpenAPI3;

async function generate(document: OpenAPI3): Promise<string> {
  return `${COMMENT_HEADER}${astToString(await openapiTS(document, { silent: true }))}`;
}

describe('generated API types', () => {
  it('schema.ts is what openapi-typescript makes from openapi.json', async () => {
    expect(
      await generate(openapiJson()),
      'src/api/schema.ts is out of date. Run: uv run python -m outreach.api.openapi --write && cd web && pnpm gen:api',
    ).toBe(committedSchema());
  });

  it('a changed openapi.json would be noticed', async () => {
    const changed = openapiJson();
    changed.components = {
      ...changed.components,
      schemas: { ...changed.components?.schemas, DriftProbe: { type: 'object', properties: {} } },
    };
    expect(await generate(changed)).not.toBe(committedSchema());
  });
});
