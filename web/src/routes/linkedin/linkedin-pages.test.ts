import { clampPage, pageGroups, pageOfPerson, peopleRange } from './linkedin-pages';

/** A company with `n` people whose ids start at `first`. */
const co = (name: string, first: number, n: number) => ({
  name,
  people: Array.from({ length: n }, (_, i) => ({ person: { id: first + i } })),
});

describe('pageGroups', () => {
  it('puts at most `per` companies on a page and never splits a company', () => {
    const groups = Array.from({ length: 23 }, (_, i) => co(`C${i}`, i * 10, (i % 4) + 1));
    const pages = pageGroups(groups, 10);
    expect(pages.map((p) => p.length)).toEqual([10, 10, 3]);
    expect(pages.flat()).toEqual(groups);
    // Each company is on exactly one page, with all its people.
    for (const g of groups) expect(pages.filter((p) => p.includes(g))).toHaveLength(1);
  });

  it('has one page per 10 companies by default and no page for no companies', () => {
    expect(pageGroups(Array.from({ length: 10 }, (_, i) => co(`C${i}`, i, 1)))).toHaveLength(1);
    expect(pageGroups(Array.from({ length: 11 }, (_, i) => co(`C${i}`, i, 1)))).toHaveLength(2);
    expect(pageGroups([])).toEqual([]);
  });
});

describe('pageOfPerson', () => {
  it('gives the 1-based page that holds the person, or null', () => {
    const pages = pageGroups([co('A', 1, 2), co('B', 10, 3), co('C', 20, 1)], 2);
    expect(pageOfPerson(pages, 1)).toBe(1);
    expect(pageOfPerson(pages, 12)).toBe(1);
    expect(pageOfPerson(pages, 20)).toBe(2);
    expect(pageOfPerson(pages, 99)).toBeNull();
    expect(pageOfPerson(pages, null)).toBeNull();
  });
});

describe('clampPage', () => {
  it('reads the page param and keeps it inside 1 to the page count', () => {
    expect(clampPage('2', 5)).toBe(2);
    expect(clampPage('9', 5)).toBe(5);
    expect(clampPage('0', 5)).toBe(1);
    expect(clampPage('-3', 5)).toBe(1);
    expect(clampPage('two', 5)).toBe(1);
    expect(clampPage('2.5', 5)).toBe(1);
    expect(clampPage('3', 0)).toBe(1);
  });
});

describe('peopleRange', () => {
  it('counts people, not companies: "Showing 4 to 7 of 9"', () => {
    const pages = pageGroups([co('A', 1, 2), co('B', 10, 1), co('C', 20, 3), co('D', 30, 1), co('E', 40, 2)], 2);
    expect(peopleRange(pages, 1)).toEqual({ from: 1, to: 3, total: 9 });
    expect(peopleRange(pages, 2)).toEqual({ from: 4, to: 7, total: 9 });
    expect(peopleRange(pages, 3)).toEqual({ from: 8, to: 9, total: 9 });
  });

  it('is zero to zero with nobody', () => {
    expect(peopleRange([], 1)).toEqual({ from: 0, to: 0, total: 0 });
  });
});
