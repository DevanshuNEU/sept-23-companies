/// <reference types="node" />
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import ts from 'typescript';

// The copy rules from the plan's Global Constraints, enforced on the source itself.
// Two checks:
//  1. No em or en dash anywhere in any source file, tests, comments and CSS included. Matched by code
//     point (a byte-level grep once missed the literal characters), and written with escapes so this file
//     passes its own check.
//  2. No guilt word in a string literal or JSX text of a non-test source file. Comments are free to
//     name the words (this one does).

const WEB = path.resolve(import.meta.dirname, '..');
const DASH = /[\u2013\u2014]/u;
const GUILT = /\b(?:streaks?|missed|overdue|behind|keep it up)\b/i;
const SCANNED = /\.(?:tsx?|css|html)$/;
const IS_TEST = /\.test\.tsx?$/;

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    if (e.name === 'node_modules' || e.name === 'dist') continue;
    const full = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...walk(full));
    else if (SCANNED.test(e.name)) out.push(full);
  }
  return out.sort();
}

const rel = (file: string) => path.relative(WEB, file);

/** Every line with an em or en dash, as `file:line`. Runs over the raw text, so comments count. */
export function dashHits(file: string, text: string): string[] {
  return text.split('\n').flatMap((line, i) => (DASH.test(line) ? [`${file}:${i + 1}`] : []));
}

/** Every string literal, template piece and JSX text in a TypeScript source, with its line. */
export function literals(file: string, text: string): { text: string; line: number }[] {
  const kind = file.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.ES2022, true, kind);
  const found: { text: string; line: number }[] = [];
  const visit = (n: ts.Node) => {
    if (
      ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n) || ts.isTemplateHead(n) ||
      ts.isTemplateMiddle(n) || ts.isTemplateTail(n) || ts.isJsxText(n)
    ) {
      found.push({ text: n.text, line: sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1 });
    }
    ts.forEachChild(n, visit);
  };
  visit(sf);
  return found;
}

export function guiltHits(file: string, text: string): string[] {
  return literals(file, text).flatMap((l) => (GUILT.test(l.text) ? [`${file}:${l.line} ${JSON.stringify(l.text)}`] : []));
}

const sources = [...walk(path.join(WEB, 'src')), ...walk(path.join(WEB, 'e2e'))];

describe('the scanners', () => {
  it('finds an em dash and an en dash by code point, in code, comments and tests', () => {
    const em = String.fromCharCode(0x2014);
    const en = String.fromCharCode(0x2013);
    expect(dashHits('a.ts', `const a = 1;\n// note ${em} here\nconst b = "x ${en} y";`)).toEqual(['a.ts:2', 'a.ts:3']);
    expect(dashHits('a.ts', 'const a = "a - b"; // a-b and a--b')).toEqual([]);
  });

  it('finds guilt words in strings, templates and JSX text, and ignores comments', () => {
    const src = [
      "// streak is a word we avoid",
      "const a = 'Your streak is over';",
      'const b = `You are ${n} days behind`;',
      "const c = <p>Keep it up!</p>;",
      "const d = \"Overdue\";",
      "const e = 'Missed Monday';",
      "const ok = 'Carried over from Monday';",
      "const alsoOk = 'a mistreated item and a behindhand one';",
    ].join('\n');
    const hits = guiltHits('a.tsx', src);
    expect(hits.map((h) => h.split(' ')[0])).toEqual(['a.tsx:2', 'a.tsx:3', 'a.tsx:4', 'a.tsx:5', 'a.tsx:6']);
  });
});

describe('copy rules', () => {
  it('scans a real set of files', () => {
    expect(sources.length).toBeGreaterThan(40);
    expect(sources.some((f) => IS_TEST.test(f))).toBe(true);
    expect(sources.some((f) => f.endsWith('.tsx'))).toBe(true);
    expect(sources.some((f) => f.endsWith('.css'))).toBe(true);
    expect(sources.some((f) => f.includes(`${path.sep}e2e${path.sep}`))).toBe(true);
  });

  it('has no em or en dash in any source, test, comment or style file', () => {
    const hits = sources.flatMap((f) => dashHits(rel(f), readFileSync(f, 'utf8')));
    expect(hits).toEqual([]);
  });

  it('has no streak, missed, overdue, behind or keep it up in UI strings', () => {
    const hits = sources
      .filter((f) => /\.tsx?$/.test(f) && !IS_TEST.test(f) && !f.includes(`${path.sep}e2e${path.sep}`))
      .flatMap((f) => guiltHits(rel(f), readFileSync(f, 'utf8')));
    expect(hits).toEqual([]);
  });
});
