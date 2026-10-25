import type { Id } from '@/api';

/** About ten companies a page: a page is a sitting's worth of work and still fits a short scroll. */
export const COMPANIES_PER_PAGE = 10;

type Grouped = { people: { person: { id: Id } }[] };

/** Splits the company groups into pages of `per` companies. A company's people are never split across pages. */
export function pageGroups<G extends Grouped>(groups: G[], per = COMPANIES_PER_PAGE): G[][] {
  const pages: G[][] = [];
  for (let i = 0; i < groups.length; i += per) pages.push(groups.slice(i, i + per));
  return pages;
}

/** The 1-based page that holds the person, or null when no page does. */
export function pageOfPerson(pages: Grouped[][], id: Id | null): number | null {
  if (id === null) return null;
  const at = pages.findIndex((page) => page.some((g) => g.people.some((p) => p.person.id === id)));
  return at === -1 ? null : at + 1;
}

/** The `?page=` value as a page from 1 to `count`. Anything that is not a whole number is page 1. */
export function clampPage(raw: string, count: number): number {
  const n = /^\d+$/.test(raw) ? Number(raw) : 1;
  return Math.min(Math.max(n, 1), Math.max(count, 1));
}

/** The people a page shows, counted across every page: "Showing 11 to 20 of 52 people". */
export function peopleRange(pages: Grouped[][], page: number): { from: number; to: number; total: number } {
  const sizes = pages.map((p) => p.reduce((n, g) => n + g.people.length, 0));
  const total = sizes.reduce((a, b) => a + b, 0);
  if (total === 0) return { from: 0, to: 0, total: 0 };
  const before = sizes.slice(0, page - 1).reduce((a, b) => a + b, 0);
  return { from: before + 1, to: before + (sizes[page - 1] ?? 0), total };
}
