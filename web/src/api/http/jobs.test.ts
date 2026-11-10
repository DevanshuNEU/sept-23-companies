import { planMethods } from './plan';
import type { Transport } from './transport';
import type { JobsView } from '../types';

const VIEW: JobsView = { asOf: '2026-11-10T15:14:00Z', rows: [], total: 0, nextAfter: null, counts: { yourLevel: 0, higher: 0 } };

describe('the job list methods', () => {
  it('reads /api/jobs with the tab, window and size, and leaves out what is off', async () => {
    const get = vi.fn(async () => VIEW);
    const t = { get, send: vi.fn() } as unknown as Transport;
    await planMethods(t).jobs!({ view: 'higher', window: 'any', size: 'big', notContacted: true, q: 'retell', after: 'c1', limit: 50 });
    expect(get).toHaveBeenCalledWith('/api/jobs?view=higher&window=any&size=big&notContacted=true&q=retell&after=c1&limit=50', undefined);
    await planMethods(t).jobs!({ view: 'your_level', window: '30', size: 'any', notContacted: false, q: '' });
    expect(get).toHaveBeenLastCalledWith('/api/jobs?view=your_level&window=30&size=any', undefined);
  });

  it('pass and go ahead post an empty body to the posting, with the request id as the key', async () => {
    const send = vi.fn(async () => ({}));
    const t = { get: vi.fn(), send } as unknown as Transport;
    const o = { requestId: 'req-12345678' };
    await planMethods(t).passJob!(9000, o);
    expect(send).toHaveBeenLastCalledWith('POST', '/api/jobs/9000/pass', {}, o);
    await planMethods(t).goAhead!(9000, o);
    expect(send).toHaveBeenLastCalledWith('POST', '/api/jobs/9000/go-ahead', {}, o);
  });

  it('check now posts an empty body with the request id as the key; the status is a plain read', async () => {
    const send = vi.fn(async () => ({}));
    const get = vi.fn(async () => ({}));
    const t = { get, send } as unknown as Transport;
    const o = { requestId: 'req-12345678' };
    await planMethods(t).checkNow!(o);
    expect(send).toHaveBeenLastCalledWith('POST', '/api/jobs/check-now', {}, o);
    await planMethods(t).checkStatus!();
    expect(get).toHaveBeenLastCalledWith('/api/jobs/check', undefined);
  });
});
