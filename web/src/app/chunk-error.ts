/** A view's file did not load (server down, deploy replaced the chunk). Reloading is the fix. */
export function isChunkLoadError(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  return (
    error.name === 'ChunkLoadError' ||
    error.message.includes('Failed to fetch dynamically imported module') ||
    error.message.includes('Importing a module script failed')
  );
}
