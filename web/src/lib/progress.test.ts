import type { BaseRates, PlanRow } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture } from '@/api/fixture/data';
import { baseRateLine, baseRateParagraph, didToday, formatRate, oneIn, rateEnough, smallSampleNote } from '@/lib/progress';

const fixture = () => new FixtureClient(buildFixture('default'));
const base = async (): Promise<BaseRates> => (await fixture().progress()).base;

describe('formatRate', () => {
  it('shows the count, the denominator and a rounded percent when there is enough data', () => {
    expect(formatRate(9, 31, true)).toBe('9 of 31 (29%)');
    expect(formatRate(2, 31, true)).toBe('2 of 31 (6%)');
  });
  it('never shows a percent under the minimum sample', () => {
    expect(formatRate(1, 2, false)).toBe('1 of 2');
    expect(formatRate(2, 9, false)).toBe('2 of 9');
    expect(formatRate(9, 31, false)).not.toContain('%');
  });
  it('says None yet with nothing to count against', () => {
    expect(formatRate(0, 0, true)).toBe('None yet');
    expect(formatRate(0, 0, false)).toBe('None yet');
  });
  it('shows zero beside its denominator and never NaN or Infinity', () => {
    expect(formatRate(0, 31, true)).toBe('0 of 31 (0%)');
    expect(formatRate(0, 5, false)).toBe('0 of 5');
    const all = [formatRate(0, 0, true), formatRate(3, 0, true), formatRate(3, 0, false), formatRate(0, 31, true), formatRate(9, 31, true)];
    for (const s of all) expect(s).not.toMatch(/NaN|Infinity/);
  });
});

describe('oneIn', () => {
  it('rounds the denominator over the count', () => {
    expect(oneIn(142, 9)).toBe(16);
  });
  it('is null with a count of zero, never 1 in 0', () => {
    expect(oneIn(30, 0)).toBeNull();
    expect(oneIn(0, 0)).toBeNull();
  });
});

describe('baseRateLine', () => {
  it('writes About 1 in N for the default fixture', async () => {
    expect(await base()).toMatchObject({ requests: 142, replies: 9, enough: true });
    expect(baseRateLine(await base())).toBe('About 1 in 16 requests gets a reply.');
  });
  it('is null when there is not enough data', async () => {
    expect(baseRateLine({ ...(await base()), enough: false })).toBeNull();
  });
  it('is null with no replies', async () => {
    expect(baseRateLine({ ...(await base()), replies: 0 })).toBeNull();
  });
  it('is null with no requests', async () => {
    expect(baseRateLine({ ...(await base()), requests: 0, replies: 0 })).toBeNull();
    expect(baseRateLine({ ...(await base()), requests: 0, replies: 3 })).toBeNull();
  });
  it('says most requests get a reply when N is 1', async () => {
    expect(baseRateLine({ ...(await base()), requests: 40, replies: 40 })).toBe('Most requests get a reply.');
    expect(baseRateLine({ ...(await base()), requests: 40, replies: 30 })).toBe('Most requests get a reply.');
  });
  it('keeps the 1 in N form from N of 2', async () => {
    expect(baseRateLine({ ...(await base()), requests: 40, replies: 20 })).toBe('About 1 in 2 requests gets a reply.');
  });
});

describe('baseRateParagraph', () => {
  it('adds the median days to the rate', async () => {
    expect(baseRateParagraph(await base())).toBe('About 1 in 16 requests gets a reply, and replies often take about 6 days.');
  });
  it('uses the singular for one day and drops the clause with no median', async () => {
    expect(baseRateParagraph({ ...(await base()), medianDaysToReply: 1 })).toBe('About 1 in 16 requests gets a reply, and replies often take about 1 day.');
    expect(baseRateParagraph({ ...(await base()), medianDaysToReply: null })).toBe('About 1 in 16 requests gets a reply.');
  });
  it('says there is not enough to go on, with the minimum, when the sample is small', async () => {
    expect(baseRateParagraph({ ...(await base()), enough: false, requests: 12 })).toBe('Not enough outcomes yet to say what is usual. It takes about 30 requests.');
  });
  it('is null with enough requests and no replies ever, never 1 in 0', async () => {
    expect(baseRateParagraph({ ...(await base()), replies: 0, medianDaysToReply: null })).toBeNull();
  });
});

describe('smallSampleNote', () => {
  it('names the count and says why no rate shows', () => {
    expect(smallSampleNote({ enough: false }, 9, 30)).toBe('Only 9 requests so far. Rates this small swing a lot, so none are shown.');
  });
  it('is null when there is enough data', () => {
    expect(smallSampleNote({ enough: true }, 142, 30)).toBeNull();
  });
  it('uses the singular for one request', () => {
    expect(smallSampleNote({ enough: false }, 1, 30)).toBe('Only 1 request so far. Rates this small swing a lot, so none are shown.');
  });
});

describe('didToday', () => {
  it('lists what the default plan has done, in order', async () => {
    const plan = await fixture().plan();
    expect(didToday(plan.rows)).toEqual([
      { label: 'Requests sent', count: 2 },
      { label: 'Applications sent', count: 2 },
    ]);
  });
  it('omits zero counts', () => {
    expect(didToday([])).toEqual([]);
  });
  it('counts a finished email row as Emails reviewed, after the other two', async () => {
    const plan = await fixture().plan();
    const rows: PlanRow[] = plan.rows.map((r) => (r.channel === 'email' ? { ...r, state: 'done' } : r));
    expect(didToday(rows)).toEqual([
      { label: 'Requests sent', count: 2 },
      { label: 'Applications sent', count: 2 },
      { label: 'Emails reviewed', count: 1 },
    ]);
  });
  it('does not count an email row that is still to do or skipped', async () => {
    const plan = await fixture().plan();
    const rows: PlanRow[] = plan.rows.map((r) => (r.channel === 'email' ? { ...r, state: 'skipped' } : r));
    expect(didToday(rows).map((d) => d.label)).not.toContain('Emails reviewed');
  });
});

describe('rateEnough', () => {
  it('needs the API to say enough and at least 20 in the stage denominator', () => {
    expect(rateEnough(true, 31)).toBe(true);
    expect(rateEnough(true, 20)).toBe(true);
    expect(rateEnough(true, 19)).toBe(false);
    expect(rateEnough(true, 2)).toBe(false);
    expect(rateEnough(false, 50)).toBe(false);
    expect(rateEnough(true, 0)).toBe(false);
  });
});
