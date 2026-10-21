import { ageDays, formatAge, formatDay, formatDayTime, formatDue, formatClockTime, formatDueDay, formatLocalTime, formatMonth, formatRecipientTime, formatWeekdayTime, parseIsoDate } from '@/lib/format';

const ASOF = '2026-11-10T15:14:00Z'; // Tuesday 10:14 in Boston

describe('formatRecipientTime', () => {
  it('formats recipient time across the DST change', () => {
    // Before the change (PDT, UTC-7) and after it (EST, UTC-5; CST, UTC-6).
    expect(formatRecipientTime('2026-10-30T17:02:00Z', 'America/Los_Angeles', { day: true })).toBe('Fri Oct 30, 10:02 PT');
    expect(formatRecipientTime('2026-11-10T15:42:00Z', 'America/New_York')).toBe('10:42 ET');
    expect(formatRecipientTime('2026-11-10T16:05:00Z', 'America/Chicago')).toBe('10:05 CT');
  });

  it('abbreviates the four US zones and Phoenix', () => {
    const at = '2026-11-10T18:00:00Z';
    expect(formatRecipientTime(at, 'America/Denver')).toBe('11:00 MT');
    expect(formatRecipientTime(at, 'America/Phoenix')).toBe('11:00 MT');
    expect(formatRecipientTime(at, 'America/Los_Angeles')).toBe('10:00 PT');
  });

  it('keeps two digits for the hour and uses 00 at midnight', () => {
    expect(formatRecipientTime('2026-11-10T13:30:00Z', 'America/New_York')).toBe('08:30 ET');
    expect(formatRecipientTime('2026-11-10T05:05:00Z', 'America/New_York')).toBe('00:05 ET');
  });

  it('falls back to the short name Intl gives for any other zone', () => {
    const out = formatRecipientTime('2026-11-10T15:42:00Z', 'Europe/London');
    expect(out).toMatch(/^15:42 \S+$/);
  });

  it('puts the day in the recipient zone, not the local one', () => {
    // 03:30Z on Nov 11 is still Nov 10 evening in Los Angeles.
    expect(formatRecipientTime('2026-11-11T03:30:00Z', 'America/Los_Angeles', { day: true })).toBe('Tue Nov 10, 19:30 PT');
  });
});

describe('formatLocalTime', () => {
  it('reads the app zone, America/New_York, with no zone suffix', () => {
    expect(formatLocalTime('2026-11-10T15:14:00Z')).toBe('10:14');
    expect(formatLocalTime('2026-10-30T14:14:00Z')).toBe('10:14');
  });
});

describe('formatClockTime', () => {
  it('is the time of day in the app zone with no leading zero, as the plan views write it', () => {
    expect(formatClockTime('2026-11-10T14:02:00Z')).toBe('9:02');
    expect(formatClockTime('2026-11-10T15:14:00Z')).toBe('10:14');
    expect(formatClockTime('2026-11-10T05:05:00Z')).toBe('0:05');
    expect(formatClockTime('2026-10-30T13:41:00Z')).toBe('9:41');
  });
});

describe('parseIsoDate', () => {
  it('splits a calendar date into numbers', () => {
    expect(parseIsoDate('2026-11-13')).toEqual({ y: 2026, m: 11, d: 13 });
  });
  it('rejects anything that is not a calendar date', () => {
    expect(() => parseIsoDate('2026-11-13T00:00:00Z')).toThrow();
    expect(() => parseIsoDate('2026-13-01')).toThrow();
    expect(() => parseIsoDate('2026-02-30')).toThrow();
  });
});

describe('formatDue', () => {
  it('formatDue reads a date as a calendar date', () => {
    expect(formatDue('2026-11-13', ASOF)).toBe('Due Fri Nov 13');
    expect(formatDue('2026-11-10', ASOF)).toBe('Due today');
  });
  it('uses the Boston calendar for today, not UTC', () => {
    // 03:30Z on Nov 11 is 22:30 on Nov 10 in Boston: Nov 10 is still today.
    expect(formatDue('2026-11-10', '2026-11-11T03:30:00Z')).toBe('Due today');
    expect(formatDue('2026-11-11', '2026-11-11T03:30:00Z')).toBe('Due Wed Nov 11');
  });
});

describe('ageDays', () => {
  it('ages count calendar days across Nov 1', () => {
    expect(ageDays('2026-10-30T14:00:00Z', ASOF)).toBe(11);
    expect(ageDays('2026-10-31T22:00:00Z', ASOF)).toBe(10);
  });
  it('counts 23:30 Boston time the day before as 1 day', () => {
    // 2026-11-09 23:30 EST is 2026-11-10T04:30Z, ten hours before ASOF.
    expect(ageDays('2026-11-10T04:30:00Z', ASOF)).toBe(1);
  });
  it('is 0 for the same Boston day', () => {
    expect(ageDays('2026-11-10T05:30:00Z', ASOF)).toBe(0); // 00:30 Boston
    expect(ageDays(ASOF, ASOF)).toBe(0);
  });
});

describe('formatAge', () => {
  it('says today, 1 day ago and N days ago', () => {
    expect(formatAge(ASOF, ASOF)).toBe('today');
    expect(formatAge('2026-11-10T04:30:00Z', ASOF)).toBe('1 day ago');
    expect(formatAge('2026-10-20T15:00:00Z', ASOF)).toBe('21 days ago');
  });
  it('treats a time after asOf as today', () => {
    expect(formatAge('2026-11-10T20:00:00Z', ASOF)).toBe('today');
  });
});

describe('formatDay', () => {
  it('reads the day of an instant in the app zone', () => {
    expect(formatDay('2026-11-06T15:00:00Z')).toBe('Fri Nov 6');
  });
  it('uses the Boston day, not the UTC day', () => {
    expect(formatDay('2026-11-07T02:30:00Z')).toBe('Fri Nov 6'); // 21:30 EST on Nov 6
    expect(formatDay('2026-11-01T05:30:00Z')).toBe('Sun Nov 1'); // 01:30 EDT, before the clocks go back
  });
});

describe('formatDueDay', () => {
  it('reads a calendar date, never shifted by a zone', () => {
    expect(formatDueDay('2026-11-10')).toBe('Tue Nov 10');
    expect(formatDueDay('2026-11-01')).toBe('Sun Nov 1');
  });
  it('does not say today, whatever the date', () => {
    expect(formatDueDay('1970-01-01')).toBe('Thu Jan 1');
  });
  it('rejects a bad date', () => {
    expect(() => formatDueDay('2026-02-30')).toThrow();
  });
});

describe('formatWeekdayTime and formatDayTime', () => {
  it('reads "Mon 08:04" and "Mon Nov 9, 08:04" in the app zone', () => {
    expect(formatWeekdayTime('2026-11-09T13:04:00Z')).toBe('Mon 08:04');
    expect(formatDayTime('2026-11-09T13:04:00Z')).toBe('Mon Nov 9, 08:04');
  });
  it('uses the Boston day, not the UTC day', () => {
    expect(formatWeekdayTime('2026-11-10T03:30:00Z')).toBe('Mon 22:30');
    expect(formatDayTime('2026-11-01T05:30:00Z')).toBe('Sun Nov 1, 01:30'); // EDT, before the clocks go back
  });
});

describe('formatMonth', () => {
  it('names the month of an instant in the app zone', () => {
    expect(formatMonth('2026-11-10T15:14:00Z')).toBe('November');
    expect(formatMonth('2026-11-01T03:30:00Z')).toBe('October'); // 23:30 EDT on Oct 31
  });
});
