import { companyMethods, searchMethods } from './company';
import type { Transport } from './transport';
import type { CompanyView, ScoreView, SearchIndex } from '../types';

const EMPTY_VIEW: CompanyView = {
  asOf: '2026-11-10T15:14:00Z',
  company: {
    id: 7, name: 'Acme', status: 'candidate', pausedAt: null, heldByTouchId: null, held: false, heldByReply: null,
    domain: null, aliases: [], hq: '',
    pickedOn: null, day: null, closed: null, revision: 1,
  },
  summary: 'A candidate, no open role; no people yet.', facts: [], sponsorshipShown: 'not_stated', mailServerOk: null, board: null,
  role: null, postings: [], people: [], touches: [], evidence: [], events: [], reviewItems: [], rulesetVersion: 'r-000000000000',
};
const SCORE: ScoreView = {
  asOf: '2026-11-10T15:14:00Z', companyId: 7, rankerVersion: 'rk-3', score: 48, rank: 2, ofCompanies: 30,
  lanes: [{ lane: 'level_role', strongest: true }],
  signals: [{
    name: 'live_role', label: 'Live role', value: 1, weight: 40, points: 40, share: 1, neutral: false, reason: 'Hiring a new grad engineer',
    confidence: 'high', basis: 'the posting was read this week', source: { label: 'New Grad Engineer', url: 'https://jobs.example/1' },
  }],
  rankReason: 'Hiring a new grad engineer (high confidence: read this week)', confidence: { level: 'high', basis: 'read this week' },
  scoredAt: '2026-11-10T08:00:00Z', previous: { rankerVersion: 'rk-2', score: 41 },
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

describe('score', () => {
  it('reads /api/companies/{id}/score and hands back the view as it came', async () => {
    const { t, get } = transport(SCORE);
    const view = await companyMethods(t).score!(7);
    expect(get).toHaveBeenCalledWith('/api/companies/7/score', undefined);
    expect(view).toEqual(SCORE);
  });

  it('a company with no score is the API\'s not_found, passed on untouched', async () => {
    const get = vi.fn(async () => { throw new Error('not_found'); });
    await expect(companyMethods({ get, send: vi.fn() } as unknown as Transport).score!(9)).rejects.toThrow('not_found');
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
