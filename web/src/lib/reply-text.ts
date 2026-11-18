/**
 * A reply as he reads it: what the person wrote, then the quoted chain and legal footer folded away.
 * The stored text is often one line (the Mail.app path collapses line breaks), so the cut is by marker, not by line.
 */
export type SplitReply = { fresh: string; quoted: string };

const MARKERS: RegExp[] = [
  // Outlook header block: "From: ... Sent: ..."
  /\bFrom:[^]{0,400}?\bSent:/,
  // Gmail and Apple Mail: "On Wed, Nov 18, 2026 at 1:13 PM Name <a@b.c> wrote:"
  /\bOn\s[^]{5,200}?\bwrote:/,
  /-{2,}\s*Original Message\s*-{2,}/i,
  /_{10,}/,
  /\b(?:E-?MAIL\s+)?CONFIDENTIALITY NOTICE\b/i,
  /\bThis (?:e-?mail|message) (?:and any attachments )?(?:is|are) (?:intended|confidential)/i,
];

export function splitReply(text: string): SplitReply {
  const whole = text.trim();
  let cut = -1;
  for (const marker of MARKERS) {
    const at = whole.search(marker);
    if (at > 0 && (cut === -1 || at < cut)) cut = at;
  }
  if (cut === -1) return { fresh: whole, quoted: '' };
  return { fresh: whole.slice(0, cut).trim(), quoted: whole.slice(cut).trim() };
}
