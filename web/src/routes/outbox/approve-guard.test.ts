// Approval is Devanshu's click or key and nothing else: the Outbox detail (one email) and the batch dialog's hook may call it.
const sources = import.meta.glob<string>('/src/**/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true });

const ALLOWED = ['/src/routes/outbox/outbox-detail.tsx', '/src/routes/outbox/use-approve-batch.ts', '/src/api/fixture/client.ts'];
const isTest = (path: string) => /\.test\.tsx?$/.test(path) || path.includes('/src/test/');

describe('approve guard', () => {
  it('reads the source files', () => {
    expect(Object.keys(sources).length).toBeGreaterThan(50);
  });

  it('only the Outbox detail, the batch hook (and the fixture client) call .approve(', () => {
    const callers = Object.entries(sources)
      .filter(([path, text]) => !isTest(path) && text.includes('.approve('))
      .map(([path]) => path)
      .sort();
    expect(callers).toContain('/src/routes/outbox/outbox-detail.tsx');
    expect(callers).toContain('/src/routes/outbox/use-approve-batch.ts');
    for (const path of callers) expect(ALLOWED).toContain(path);
  });
});

describe('batch approve guard', () => {
  it('only the Outbox list opens the batch (useApproveBatch is used nowhere else)', () => {
    const users = Object.entries(sources)
      .filter(([path, text]) => !isTest(path) && text.includes('useApproveBatch(') && !path.endsWith('use-approve-batch.ts'))
      .map(([path]) => path);
    expect(users).toEqual(['/src/routes/outbox/outbox-list.tsx']);
  });
});
