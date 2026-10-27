/** The calm palette a company's tile is drawn from (tokens `--mono-<hue>-bg` and `-fg` in globals.css, 6:1 or more). */
export const MONOGRAM_HUES = ['teal', 'cyan', 'green', 'amber', 'orange', 'rose', 'slate', 'olive'] as const;
export type MonogramHue = (typeof MONOGRAM_HUES)[number];

/** A stable hue from the name (FNV-1a over the trimmed, lower-cased name), so a company is the same colour every day. */
export function monogramHue(name: string): MonogramHue {
  let h = 0x811c9dc5;
  for (const ch of name.trim().toLowerCase()) {
    h ^= ch.codePointAt(0) ?? 0;
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return MONOGRAM_HUES[h % MONOGRAM_HUES.length] as MonogramHue;
}
