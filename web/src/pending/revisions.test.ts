import { QueryClient } from '@tanstack/react-query';
import { newestRevision, noteRevision, withRevision } from './revisions';

describe('revisions', () => {
  it('keeps the newest revision noted, per query client', () => {
    const a = new QueryClient();
    noteRevision(a, 'message:1', 3);
    noteRevision(a, 'message:1', 2);
    expect(newestRevision(a, 'message:1')).toBe(3);
    expect(newestRevision(a, 'message:1', 5, undefined)).toBe(5);
    expect(newestRevision(a, 'message:2')).toBeUndefined();
    expect(newestRevision(new QueryClient(), 'message:1')).toBeUndefined();
  });

  it('reads first on a normal commit', async () => {
    const send = vi.fn((r: number) => Promise.resolve(r));
    await expect(withRevision({ requestId: 'x' }, () => 1, () => Promise.resolve(4), send)).resolves.toBe(4);
    expect(send).toHaveBeenCalledWith(4);
  });

  it('sends at once with the known revision when the tab is closing, and reads only when none is known', async () => {
    const send = vi.fn((r: number) => Promise.resolve(r));
    const read = vi.fn(() => new Promise<number>(() => {}));
    void withRevision({ requestId: 'x', keepalive: true }, () => 2, read, send);
    // Called in the same task: nothing awaited before it.
    expect(send).toHaveBeenCalledWith(2);
    expect(read).not.toHaveBeenCalled();

    void withRevision({ requestId: 'y', keepalive: true }, () => undefined, read, send);
    expect(read).toHaveBeenCalledTimes(1);
  });
});
