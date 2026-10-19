/// <reference types="node" />
import { readFileSync } from 'node:fs';
import path from 'node:path';

// The CSS is read as text: Vitest's CSS pipeline would hand back an empty string for an import.
const css = readFileSync(path.resolve(import.meta.dirname, 'globals.css'), 'utf8');

type Rgb = [number, number, number];
type Tokens = Record<string, string>;

function block(selector: string): Tokens {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`No ${selector} block in globals.css`);
  const end = css.indexOf('}', start);
  const body = css.slice(start, end);
  const tokens: Tokens = {};
  for (const m of body.matchAll(/--([a-z-]+):\s*([^;]+);/g)) {
    tokens[m[1] as string] = (m[2] as string).trim();
  }
  return tokens;
}

function hex(value: string): Rgb {
  const m = /^#([0-9a-f]{6})$/i.exec(value);
  if (!m) throw new Error(`Not a 6-digit hex: ${value}`);
  const n = parseInt(m[1] as string, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

// Resolves a hex or `rgb(r g b / a)` token to an opaque color by compositing over `under`.
function resolve(value: string, under: Rgb): Rgb {
  const m = /^rgb\((\d+) (\d+) (\d+) \/ ([\d.]+)\)$/.exec(value);
  if (!m) return hex(value);
  const a = Number(m[4]);
  const src = [Number(m[1]), Number(m[2]), Number(m[3])];
  return [0, 1, 2].map((i) => (src[i] as number) * a + under[i]! * (1 - a)) as Rgb;
}

function luminance([r, g, b]: Rgb): number {
  const lin = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

function contrast(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

const STATUSES = ['ok', 'warn', 'bad', 'info', 'idle'] as const;

describe.each([
  ['light', ':root'],
  ['dark', '.dark'],
] as const)('%s theme tokens', (_name, selector) => {
  const t = block(selector);
  const bg = hex(t['background'] as string);
  const ratio = (fg: string, under: string) => {
    const base = resolve(under, bg);
    return contrast(resolve(fg, base), base);
  };

  it('foreground on background is at least 4.5', () => {
    expect(ratio(t['foreground'] as string, t['background'] as string)).toBeGreaterThanOrEqual(4.5);
  });

  it.each(['background', 'muted', 'subtle'])('muted-foreground on %s is at least 4.5', (surface) => {
    expect(ratio(t['muted-foreground'] as string, t[surface] as string)).toBeGreaterThanOrEqual(4.5);
  });

  it.each(STATUSES)('%s text on its soft background is at least 4.5', (status) => {
    expect(ratio(t[status] as string, t[`${status}-bg`] as string)).toBeGreaterThanOrEqual(4.5);
  });

  it('defines every status token', () => {
    for (const s of STATUSES) {
      for (const suffix of ['', '-bg', '-bd', '-dot']) {
        expect(t[`${s}${suffix}`], `${s}${suffix}`).toBeDefined();
      }
    }
  });
});

describe('spec section 10 values', () => {
  it('uses the darker light muted-foreground', () => {
    expect(block(':root')['muted-foreground']).toBe('#6b6b75');
  });

  it('keeps the spec dark muted-foreground', () => {
    expect(block('.dark')['muted-foreground']).toBe('#a1a1aa');
  });
});
