import { periodRange } from './period-label';

describe('periodRange', () => {
  it('names one month once', () => {
    expect(periodRange({ from: '2026-11-04', to: '2026-11-10' })).toBe('Nov 4 to 10');
  });
  it('names both months across a month end', () => {
    expect(periodRange({ from: '2026-10-28', to: '2026-11-03' })).toBe('Oct 28 to Nov 3');
  });
});
