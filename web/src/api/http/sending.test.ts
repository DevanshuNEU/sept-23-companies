import { sendingMethods } from './sending';
import type { Transport } from './transport';

const o = { requestId: 'req-12345678' };

describe('sendingMethods', () => {
  it('a pause reads the sender revision, then posts it', async () => {
    const get = vi.fn(async () => ({ revision: 4 }));
    const send = vi.fn(async () => ({ state: 'paused_by_you', revision: 5 }));
    const t = { get, send } as unknown as Transport;
    expect(await sendingMethods(t).pauseSending!(o)).toEqual({ state: 'paused_by_you', revision: 5 });
    expect(get).toHaveBeenCalledWith('/api/sending', undefined);
    expect(send).toHaveBeenCalledWith('POST', '/api/sending/pause', { revision: 4 }, o);
  });

  it('a resume says whether he acknowledged the hard block', async () => {
    const get = vi.fn(async () => ({ revision: 6 }));
    const send = vi.fn(async () => ({ state: 'running', revision: 7 }));
    const t = { get, send } as unknown as Transport;
    await sendingMethods(t).resumeSending!(false, o);
    expect(send).toHaveBeenLastCalledWith('POST', '/api/sending/resume', { revision: 6, acknowledgeBlock: false }, o);
    await sendingMethods(t).resumeSending!(true, o);
    expect(send).toHaveBeenLastCalledWith('POST', '/api/sending/resume', { revision: 6, acknowledgeBlock: true }, o);
  });

  it('a refused resume reaches the caller', async () => {
    const t = { get: vi.fn(async () => ({ revision: 1 })), send: vi.fn(async () => { throw new Error('not_allowed'); }) } as unknown as Transport;
    await expect(sendingMethods(t).resumeSending!(false, o)).rejects.toThrow('not_allowed');
  });
});
