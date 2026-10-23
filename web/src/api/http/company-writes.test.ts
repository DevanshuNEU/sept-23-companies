import { companyWriteMethods } from './company-writes';
import type { Transport } from './transport';
import type { PersonRef } from '../types';

const o = { requestId: 'req-12345678' };
const sender = (answer: unknown) => {
  const send = vi.fn(async () => answer);
  return { t: { get: vi.fn(), send } as unknown as Transport, send };
};

describe('companyWriteMethods', () => {
  it('pause, close and reopen post the revision to the company and resolve to nothing', async () => {
    const { t, send } = sender({ revision: 4 });
    const m = companyWriteMethods(t);
    expect(await m.pauseCompany!(7, 3, o)).toBeUndefined();
    expect(send).toHaveBeenLastCalledWith('POST', '/api/companies/7/pause', { revision: 3 }, o);
    expect(await m.closeCompany!(7, 'no_sponsorship', 'Told me on a call', 3, o)).toBeUndefined();
    expect(send).toHaveBeenLastCalledWith('POST', '/api/companies/7/close', { reason: 'no_sponsorship', note: 'Told me on a call', revision: 3 }, o);
    expect(await m.reopenCompany!(7, 3, o)).toBeUndefined();
    expect(send).toHaveBeenLastCalledWith('POST', '/api/companies/7/reopen', { revision: 3 }, o);
  });

  it('resume answers with how many emails were waiting', async () => {
    const { t, send } = sender({ released: 2, revision: 5 });
    expect(await companyWriteMethods(t).resumeCompany!(7, 4, o)).toEqual({ released: 2 });
    expect(send).toHaveBeenCalledWith('POST', '/api/companies/7/resume', { revision: 4 }, o);
  });

  it('the role kind posts to the person and answers with the person', async () => {
    const person: PersonRef = { id: 12, name: 'Dana Whitcomb', title: 'Recruiter', roleKind: 'engineer', email: null, linkedinUrl: null, revision: 2 };
    const { t, send } = sender(person);
    expect(await companyWriteMethods(t).setRoleKind!(12, 'engineer', 1, o)).toEqual(person);
    expect(send).toHaveBeenCalledWith('POST', '/api/people/12/role-kind', { kind: 'engineer', revision: 1 }, o);
  });

  it('a refused write reaches the caller', async () => {
    const send = vi.fn(async () => { throw new Error('not_allowed'); });
    const t = { get: vi.fn(), send } as unknown as Transport;
    await expect(companyWriteMethods(t).pauseCompany!(7, 1, o)).rejects.toThrow('not_allowed');
  });
});
