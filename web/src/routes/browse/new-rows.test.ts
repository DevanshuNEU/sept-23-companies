import type { JobRow } from '@/api';
import { heldRows, isNew, LAST_OPENED_KEY, markOpened, readLastOpened } from './new-rows';

const job = (postingId: number, o: Partial<JobRow> = {}): JobRow => ({
  postingId, title: `Software Engineer ${postingId}`, url: `https://jobs.example/${postingId}`, location: 'Remote (US)', levelTag: 'new grad',
  listAt: '2026-11-10T15:00:00Z', postedAt: '2026-11-10T15:00:00Z', firstSeenAt: '2026-11-10T15:05:00Z', seenGapS: 1800, firstRead: false,
  repostedFrom: null, companyId: 7001, companyName: 'Retell AI', size: 45, big: false, saysNoSponsorship: false, everify: false, yc: false,
  contactedOn: null, ...o,
});
const ids = (rows: JobRow[]) => rows.map((r) => r.postingId);

describe('heldRows', () => {
  const shown = [job(1), job(2), job(3)];
  const incoming = [job(10), job(11), job(1), job(2), job(3)];

  it('shows new rows at once at the top', () => {
    expect(heldRows(shown, incoming, true, false)).toEqual({ rows: incoming, waiting: 0 });
  });

  it('holds new rows while scrolled', () => {
    const { rows, waiting } = heldRows(shown, incoming, false, false);
    expect(ids(rows)).toEqual([1, 2, 3]);
    expect(waiting).toBe(2);
  });

  it('holds new rows while a row is open, even at the top', () => {
    const { rows, waiting } = heldRows(shown, incoming, true, true);
    expect(ids(rows)).toEqual([1, 2, 3]);
    expect(waiting).toBe(2);
  });

  it('a held row shows what the server says now', () => {
    const contacted = job(2, { contactedOn: '2026-11-10' });
    const { rows } = heldRows(shown, [job(10), job(1), contacted, job(3)], false, true);
    expect(rows[1]).toBe(contacted);
  });

  it('a row the server no longer lists (passed, or closed) leaves even while held', () => {
    const { rows, waiting } = heldRows(shown, [job(10), job(1), job(3)], false, true);
    expect(ids(rows)).toEqual([1, 3]);
    expect(waiting).toBe(1);
  });

  it('rows after the last one shown are the next page he asked for: they join at the end and are not counted', () => {
    const { rows, waiting } = heldRows(shown, [job(10), job(1), job(2), job(3), job(4), job(5)], false, true);
    expect(ids(rows)).toEqual([1, 2, 3, 4, 5]);
    expect(waiting).toBe(1);
  });

  it('a new row that lands between shown rows waits too: the list never moves under him', () => {
    const { rows, waiting } = heldRows(shown, [job(1), job(12), job(2), job(3)], false, true);
    expect(ids(rows)).toEqual([1, 2, 3]);
    expect(waiting).toBe(1);
  });

  it('with more pages on the server, shown rows pushed past the loaded window stay: they are not gone', () => {
    const tail = [job(1), job(2), job(3), job(4), job(5)];
    const { rows, waiting } = heldRows(tail, [job(10), job(1), job(2), job(3), job(4)], false, true, true);
    expect(ids(rows)).toEqual([1, 2, 3, 4, 5]);
    expect(waiting).toBe(1);
  });

  it('with no more pages, a shown row missing at the end is gone', () => {
    const tail = [job(1), job(2), job(3), job(4), job(5)];
    expect(ids(heldRows(tail, [job(10), job(1), job(2), job(3), job(4)], false, true, false).rows)).toEqual([1, 2, 3, 4]);
  });

  it('a passed last row still leaves when the next page moves a row into the window', () => {
    const { rows } = heldRows(shown, [job(1), job(2), job(6)], false, true, true);
    expect(ids(rows)).toEqual([1, 2, 6]);
  });

  it('with nothing shown yet, everything shows', () => {
    expect(heldRows([], incoming, false, true)).toEqual({ rows: incoming, waiting: 0 });
  });
});

describe('isNew', () => {
  const since = '2026-11-10T15:00:00.000Z';

  it('marks rows first seen since the last visit as new', () => {
    expect(isNew(job(1, { firstSeenAt: '2026-11-10T15:05:00Z' }), since)).toBe(true);
    expect(isNew(job(2, { firstSeenAt: '2026-11-10T14:59:59Z' }), since)).toBe(false);
    expect(isNew(job(3, { firstSeenAt: '2026-11-10T15:00:00Z' }), since)).toBe(false);
  });

  it('a first read or a repost is never new', () => {
    expect(isNew(job(1, { firstRead: true }), since)).toBe(false);
    expect(isNew(job(2, { repostedFrom: '2026-10-02T14:30:00Z' }), since)).toBe(false);
  });

  it('nothing is new on the first visit', () => {
    expect(isNew(job(1), null)).toBe(false);
  });
});

describe('the last time he opened the list', () => {
  beforeEach(() => window.localStorage.clear());

  it('is kept in the browser and read back', () => {
    expect(readLastOpened()).toBeNull();
    markOpened(new Date('2026-11-10T15:14:00Z'));
    expect(window.localStorage.getItem(LAST_OPENED_KEY)).toBe('2026-11-10T15:14:00.000Z');
    expect(readLastOpened()).toBe('2026-11-10T15:14:00.000Z');
  });

  it('a value that is not a time reads as never', () => {
    window.localStorage.setItem(LAST_OPENED_KEY, 'yesterday');
    expect(readLastOpened()).toBeNull();
  });
});
