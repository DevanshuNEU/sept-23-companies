/// <reference types="node" />
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { render } from '@testing-library/react';
import { WinMark } from '@/components/win-mark';

describe('WinMark', () => {
  it('WinMark is aria-hidden', () => {
    const { container } = render(<WinMark />);
    const svg = container.querySelector('svg');
    expect(svg).toHaveAttribute('aria-hidden', 'true');
    expect(svg?.querySelector('path')).toHaveClass('win-draw');
  });
});

describe('the win motion', () => {
  // Read as text, as tokens.test.ts does: Vitest's CSS pipeline hands back an empty string for an import.
  const css = readFileSync(path.resolve(import.meta.dirname, '../styles/globals.css'), 'utf8');

  function ruleBody(selector: string): string {
    // A top-level rule starts its line, so `.win-draw` never matches inside `.win-in .win-draw`.
    const start = css.indexOf(`\n${selector} {`);
    if (start < 0) throw new Error(`No ${selector} rule in globals.css`);
    return css.slice(start, css.indexOf('}', start));
  }

  it('globals.css has win-draw and win-in inside the reduced motion override scope', () => {
    expect(css).toMatch(/@keyframes win-draw\s*\{/);
    expect(css).toMatch(/@keyframes win-in\s*\{/);
    const draw = ruleBody('.win-in .win-draw');
    const fade = ruleBody('.win-in');
    expect(draw).toMatch(/animation:\s*win-draw 240ms/);
    expect(fade).toMatch(/animation:\s*win-in 240ms/);
    // The draw runs only inside the banner's entrance, so a remount of the mark alone does not replay it.
    expect(ruleBody('.win-draw')).not.toMatch(/animation/);
    // The override is `* { animation: none !important }`: it beats both rules only while neither is !important.
    expect(draw).not.toContain('!important');
    expect(fade).not.toContain('!important');
    const media = css.indexOf('@media (prefers-reduced-motion: reduce)');
    expect(media).toBeGreaterThan(-1);
    const override = css.slice(media, css.indexOf('}', media));
    expect(override).toMatch(/\*\s*,/);
    expect(override).toContain('animation: none !important');
    // No new token rides along with the motion.
    expect(draw + fade).not.toMatch(/--[a-z]/);
  });
});
