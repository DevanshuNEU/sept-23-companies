/**
 * The address to link to, or undefined when it is not a web address. Only http: and https: pass: a stored
 * `javascript:` or `data:` URL (or a relative one, or garbage) must never become a link or open a window.
 */
export function safeHref(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  try {
    const { protocol } = new URL(url);
    return protocol === 'http:' || protocol === 'https:' ? url : undefined;
  } catch {
    return undefined;
  }
}

/** Opens a web address in a new tab without handing over the opener. False (and nothing opens) when it is not http or https. */
export function openExternal(url: string | null | undefined): boolean {
  const href = safeHref(url);
  if (!href) return false;
  window.open(href, '_blank', 'noopener,noreferrer');
  return true;
}
