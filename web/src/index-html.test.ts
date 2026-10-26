/// <reference types="node" />
import { readFileSync } from 'node:fs';
import path from 'node:path';

const html = readFileSync(path.resolve(import.meta.dirname, '../index.html'), 'utf8');

describe('index.html', () => {
  it('is titled Knock', () => {
    expect(html).toContain('<title>Knock</title>');
  });

  it('carries an inline SVG favicon of the K tile, not an empty data URL', () => {
    expect(html).not.toContain('href="data:,"');
    const m = /<link rel="icon" type="image\/svg\+xml" href="data:image\/svg\+xml,([^"]+)"/.exec(html);
    expect(m).not.toBeNull();
    const svg = decodeURIComponent(m![1] as string);
    expect(svg).toContain('<svg');
    expect(svg).toContain('>K<');
    expect(svg).toContain('#5b48d9');
  });
});
