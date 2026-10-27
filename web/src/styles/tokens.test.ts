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
const MONOGRAM_HUES = ['teal', 'cyan', 'green', 'amber', 'orange', 'rose', 'slate', 'olive'] as const;

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

  it.each(['background', 'muted', 'subtle', 'card', 'popover', 'sidebar'])('muted-foreground on %s is at least 4.5', (surface) => {
    expect(ratio(t['muted-foreground'] as string, t[surface] as string)).toBeGreaterThanOrEqual(4.5);
  });

  it('brand-foreground on brand is at least 4.5', () => {
    expect(ratio(t['brand-foreground'] as string, t['brand'] as string)).toBeGreaterThanOrEqual(4.5);
  });

  it.each(['background', 'card', 'muted', 'sidebar', 'popover'])('brand on %s is at least 4.5', (surface) => {
    expect(ratio(t['brand'] as string, t[surface] as string)).toBeGreaterThanOrEqual(4.5);
  });

  it('the focus ring is the brand colour and reads at 3:1 on the canvas and on cards', () => {
    expect(t['ring']).toBe(t['brand']);
    expect(t['sidebar-ring']).toBe(t['brand']);
    expect(ratio(t['ring'] as string, t['background'] as string)).toBeGreaterThanOrEqual(3);
    expect(ratio(t['ring'] as string, t['card'] as string)).toBeGreaterThanOrEqual(3);
  });

  it('cards sit one step above the canvas and the sidebar one step below it', () => {
    const lum = (key: string) => luminance(hex(t[key] as string));
    expect(lum('card')).toBeGreaterThan(lum('background'));
    expect(lum('background')).toBeGreaterThan(lum('sidebar'));
  });

  it.each(STATUSES)('%s text on its soft background is at least 4.5', (status) => {
    expect(ratio(t[status] as string, t[`${status}-bg`] as string)).toBeGreaterThanOrEqual(4.5);
  });

  it.each(MONOGRAM_HUES)('the %s monogram text on its tile is at least 4.5, and the tile is not the brand', (hue) => {
    expect(ratio(t[`mono-${hue}-fg`] as string, t[`mono-${hue}-bg`] as string)).toBeGreaterThanOrEqual(4.5);
    expect(t[`mono-${hue}-bg`]).not.toBe(t['brand']);
    expect(t[`mono-${hue}-fg`]).not.toBe(t['brand']);
  });

  it('defines every status token', () => {
    for (const s of STATUSES) {
      for (const suffix of ['', '-bg', '-bd', '-dot']) {
        expect(t[`${s}${suffix}`], `${s}${suffix}`).toBeDefined();
      }
    }
  });
});

// The look PR's token table (docs/design/README.md, section 10).
const TABLE: Record<string, [light: string, dark: string]> = {
  background: ['#f8f8f9', '#0b0b0e'],
  sidebar: ['#f2f2f4', '#09090b'],
  subtle: ['#f3f3f5', '#101013'],
  card: ['#ffffff', '#141418'],
  popover: ['#ffffff', '#1a1a20'],
  muted: ['#eeeef1', '#23232a'],
  accent: ['#f0f0f3', '#1d1d23'],
  border: ['#e2e2e7', 'rgb(255 255 255 / 0.09)'],
  input: ['#c4c4cc', '#52525b'],
  'muted-foreground': ['#66666f', '#a1a1aa'],
  brand: ['#5b48d9', '#9187f7'],
  ring: ['#5b48d9', '#9187f7'],
  'brand-foreground': ['#ffffff', '#16122e'],
};

describe('spec section 10 values', () => {
  it.each(Object.entries(TABLE))('%s', (name, [light, dark]) => {
    expect(block(':root')[name]).toBe(light);
    expect(block('.dark')[name]).toBe(dark);
  });

  it('maps brand and brand-foreground into the Tailwind theme', () => {
    expect(css).toContain('--color-brand: var(--brand);');
    expect(css).toContain('--color-brand-foreground: var(--brand-foreground);');
  });

  it('defines the serif note face', () => {
    expect(css).toContain('--font-serif: ui-serif, "New York", Georgia, serif;');
  });
});

describe('motion tokens', () => {
  const reduced = css.slice(css.indexOf('@media (prefers-reduced-motion: reduce)'));

  it.each(['knock', 'ripple', 'shimmer'])('defines the %s animation', (name) => {
    expect(css).toContain(`@keyframes ${name}`);
  });

  it('turns every animation off under reduced motion', () => {
    expect(reduced).toMatch(/animation:\s*none\s*!important/);
  });
});
