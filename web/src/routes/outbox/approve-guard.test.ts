// Approval is Devanshu's click or key and nothing else: one component may call it.
const sources = import.meta.glob<string>('/src/**/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true });

const ALLOWED = ['/src/routes/outbox/outbox-detail.tsx', '/src/api/fixture/client.ts'];
const isTest = (path: string) => /\.test\.tsx?$/.test(path) || path.includes('/src/test/');

describe('approve guard', () => {
  it('reads the source files', () => {
    expect(Object.keys(sources).length).toBeGreaterThan(50);
  });

  it('only the Outbox detail (and the fixture client) call .approve(', () => {
    const callers = Object.entries(sources)
      .filter(([path, text]) => !isTest(path) && text.includes('.approve('))
      .map(([path]) => path)
      .sort();
    expect(callers).toContain('/src/routes/outbox/outbox-detail.tsx');
    for (const path of callers) expect(ALLOWED).toContain(path);
  });
});
