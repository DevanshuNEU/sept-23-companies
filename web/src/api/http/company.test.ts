import { companyMethods, searchMethods } from './company';
import type { Transport } from './transport';
import type { CompanyView, SearchIndex } from '../types';

const EMPTY_VIEW: CompanyView = {
  asOf: '2026-11-10T15:14:00Z',
  company: {
    id: 7, name: 'Acme', status: 'candidate', pausedAt: null, heldByTouchId: null, domain: null, aliases: [], hq: '',
    pickedOn: null, day: null, closed: null, revision: 1,
  },
  summary: 'A candidate, no open role; no people yet.', facts: [], sponsorshipShown: 'not_stated', mailServerOk: null, board: null,
  role: null, postings: [], people: [], touches: [], evidence: [], events: [], reviewItems: [], rulesetVersion: 'r-000000000000',
};
const EMPTY_INDEX: SearchIndex = { asOf: '2026-11-10T15:14:00Z', companies: [], people: [] };

const transport = (answer: unknown) => {
  const get = vi.fn(async () => answer);
  return { t: { get, send: vi.fn() } as unknown as Transport, get };
};

describe('companyMethods', () => {
  it('reads /api/companies/{id} and hands back the view as it came', async () => {
    const { t, get } = transport(EMPTY_VIEW);
    const view = await companyMethods(t).company!(7);
    expect(get).toHaveBeenCalledWith('/api/companies/7', undefined);
    expect(view).toEqual(EMPTY_VIEW);
  });

  it('a company with nothing is a valid view: empty lists and no role or board', async () => {
    const { t } = transport(EMPTY_VIEW);
    const view = await companyMethods(t).company!(7);
    expect([view.postings, view.people, view.touches, view.evidence, view.events, view.reviewItems]).toEqual([[], [], [], [], [], []]);
    expect(view.role).toBeNull();
    expect(view.board).toBeNull();
  });
});

describe('searchMethods', () => {
  it('reads the index', async () => {
    const { t, get } = transport(EMPTY_INDEX);
    expect(await searchMethods(t).searchIndex!()).toEqual(EMPTY_INDEX);
    expect(get).toHaveBeenCalledWith('/api/search/index', undefined);
  });

  it('sends the query for posting and company search, encoded', async () => {
    const { t, get } = transport([]);
    expect(await searchMethods(t).searchPostings!('member of tech & staff')).toEqual([]);
    expect(get).toHaveBeenLastCalledWith('/api/search/postings?q=member+of+tech+%26+staff', undefined);
    expect(await searchMethods(t).searchCompanies!('acme')).toEqual([]);
    expect(get).toHaveBeenLastCalledWith('/api/search/companies?q=acme', undefined);
  });

  it('an empty answer is an empty list, not an error', async () => {
    const { t } = transport([]);
    expect(await searchMethods(t).searchPostings!('')).toEqual([]);
  });
});
