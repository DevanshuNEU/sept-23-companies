import { safeHref } from '@/lib/safe-href';
import type { PlanView, ProgressView, ScoreView } from '../types';
import { FixtureClient } from './client';
import { buildFixture, SCENARIOS, type Scenario } from './data';
import { tomorrowText, winsOnDay } from './plan-data';

const client = (scenario: Scenario = 'default') => new FixtureClient(buildFixture(scenario));
const row = (plan: PlanView, n: number) => {
  const r = plan.rows[n - 1];
  if (!r) throw new Error(`No row ${n}`);
  return r;
};
const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
const counts = (p: ProgressView['week']) => Object.fromEntries(p.stages.map((s) => [s.stage, s.count]));

describe("plan fixture: tomorrow's draft", () => {
  it('lists people first, then find-someone rows, more than ten of them and inside the room', async () => {
    const { tomorrow } = await client().plan();
    const kinds = tomorrow.preview.map((p) => p.findSomeone);
    expect(kinds.indexOf(true)).toBeGreaterThan(0);
    expect(kinds.slice(kinds.indexOf(true)).every(Boolean)).toBe(true);
    expect(tomorrow.preview.every((p) => p.findSomeone === (p.personName === null))).toBe(true);
    expect(tomorrow.preview.some((p) => p.channel === 'email')).toBe(true);
    expect(tomorrow.preview.length).toBeGreaterThan(10);
    expect(tomorrow.preview.length).toBeLessThanOrEqual(tomorrow.total);
    expect(tomorrow.text).toBe(tomorrowText('Tomorrow', tomorrow.total, tomorrow.preview.length));
  });

  it('says "below" only when the draft has rows, as the API does', () => {
    expect(tomorrowText('Monday', 25, 3)).toBe("Monday's plan builds at 08:00. Room for 25 people. A draft of who it would propose is below.");
    expect(tomorrowText('Tomorrow', 1, 0)).toBe("Tomorrow's plan builds at 08:00. Room for 1 person.");
  });
});

describe('plan fixture: default', () => {
  it('default plan has 7 rows in the listed order, done 2, total 7', async () => {
    const plan = await client().plan();
    expect(plan.date).toBe('2026-11-10');
    expect(plan.asOf).toBe('2026-11-10T15:14:00Z');
    expect(plan.rows.map((r) => r.position)).toEqual([1, 2, 3, 4, 5, 6, 7]);
    expect(plan.rows.map((r) => r.company.name)).toEqual(['Resend', 'Baseten', 'Pylon', 'Inngest', 'Cerebrium', 'Turso', 'Mintlify']);
    expect(plan.rows.map((r) => r.person?.person.name ?? null)).toEqual(['Dana Whitfield', 'Tomas Reyes', 'Lena Ortiz', 'Jonah Park', null, 'Ines Duarte', expect.any(String)]);
    expect(plan.rows.map((r) => r.role?.title ?? null)).toEqual([
      'Software Engineer, New Grad', 'Forward Deployed Engineer, New Grad', 'Software Engineer I', 'Software Engineer II',
      'Member of Technical Staff', 'Software Engineering Intern', null,
    ]);
    expect(plan.rows.map((r) => r.lane).slice(0, 3)).toEqual(['level_role', 'level_role', 'level_role']);
    expect(row(plan, 6).lane).toBe('intern_junior');
    expect(row(plan, 6).person?.person.roleKind).toBe('founder');
    expect(row(plan, 7).lane).toBe('reason_no_role');
    expect(plan.rows.map((r) => r.state)).toEqual(['done', 'done', 'todo', 'todo', 'todo', 'todo', 'todo']);
    expect(plan.done).toBe(2);
    expect(plan.total).toBe(7);
    expect(plan.notReady).toBeNull();
    expect(plan.linkedinWeek).toEqual({ used: 26, limit: 150, hardUsed: 31, hardLimit: 200 });
    expect(plan.week).toEqual({ requests: 31, accepts: 9, replies: 2 });
    expect(plan.tomorrow.total).toBeGreaterThan(0);
  });

  it('rows 1 and 2 have both steps logged, row 3 has none', async () => {
    const plan = await client().plan();
    for (const n of [1, 2]) {
      expect(row(plan, n).steps.map((s) => s.kind)).toEqual(['request', 'apply']);
      expect(row(plan, n).steps.every((s) => s.touchId !== null && s.at !== null)).toBe(true);
    }
    expect(row(plan, 3).steps.map((s) => [s.kind, s.touchId, s.at])).toEqual([['request', null, null], ['apply', null, null]]);
    expect(row(plan, 1).role?.title).toBe('Software Engineer, New Grad');
  });

  it('row 3 note is 168 of 300 with two linked facts and a confirmed line', async () => {
    const note = row(await client().plan(), 3).note;
    expect(note).not.toBeNull();
    expect(note?.chars).toBe(168);
    expect(note?.maxChars).toBe(300);
    expect(note?.facts).toHaveLength(2);
    expect(note?.facts.every((f) => f.source.url.includes('.example'))).toBe(true);
    expect(note?.reasonState).toBe('confirmed');
    expect(note?.needsYourLine).toBe(false);
  });

  it('row 4 needs your line: proposed with a proposed reason line', async () => {
    const note = row(await client().plan(), 4).note;
    expect(note?.reasonState).toBe('proposed');
    expect(note?.needsYourLine).toBe(true);
    expect(note?.reasonLine).not.toBe('');
  });

  it('row 5 has no person and a LinkedIn people-search link', async () => {
    const plan = await client().plan();
    const r = row(plan, 5);
    expect(r.person).toBeNull();
    expect(r.note).toBeNull();
    expect(r.findSomeone?.searchUrl.startsWith('https://www.linkedin.com/search/results/people/')).toBe(true);
    expect(r.findSomeone?.handoff).not.toBe('');
    plan.rows.filter((x) => x.position !== 5).forEach((x) => expect(x.findSomeone).toBeNull());
  });

  it('row 7 is an email row', async () => {
    const r = row(await client().plan(), 7);
    expect(r.channel).toBe('email');
    expect(r.emailTouchId).not.toBeNull();
    expect(r.role).toBeNull();
    expect(r.applyUrl).toBeNull();
    expect(r.company.name).toBe('Mintlify');
  });

  it('only linkedin rows have no emailTouchId, and only rows with a role have an apply link', async () => {
    const plan = await client().plan();
    plan.rows.filter((r) => r.channel === 'linkedin_request').forEach((r) => expect(r.emailTouchId).toBeNull());
    plan.rows.forEach((r) => expect(r.applyUrl).toBe(r.role?.url ?? null));
  });

  it('every note is within its limit and every fact link is safe', async () => {
    for (const scenario of SCENARIOS) {
      const plan = await new FixtureClient(buildFixture(scenario)).plan().catch(() => null);
      for (const r of plan?.rows ?? []) {
        if (!r.note) continue;
        expect(r.note.chars).toBe(r.note.text.length);
        expect(r.note.chars).toBeLessThanOrEqual(r.note.maxChars);
        expect(r.note.needsYourLine).toBe(r.note.reasonState === 'proposed');
        for (const f of r.note.facts) expect(safeHref(f.source.url)).toBeDefined();
      }
    }
    const withNotes = (await client().plan()).rows.filter((r) => r.note);
    expect(withNotes.length).toBeGreaterThanOrEqual(5);
  });

  it('every row has a reason and at least one source link', async () => {
    const plan = await client().plan();
    for (const r of plan.rows) {
      expect(r.reason.length).toBeGreaterThan(0);
      expect(r.reasonSources.length).toBeGreaterThanOrEqual(1);
      r.reasonSources.forEach((s) => expect(safeHref(s.url)).toBeDefined());
    }
  });

  it('shell carries plan and wins', async () => {
    expect(await client().shell()).toMatchObject({ plan: { done: 2, total: 7 }, winsUnseen: 0 });
    expect((await client('win').shell()).winsUnseen).toBe(1);
  });

  it('the outcome prompt has 2 touches older than 7 days', async () => {
    const p = await client().outcomePrompt();
    expect(p.touches).toHaveLength(2);
    p.touches.forEach((t) => expect(t.ageDays).toBeGreaterThan(7));
  });

  it('wins: 5 kept, one interview at Distyl AI, one referral, three replies, none unseen', async () => {
    const w = await client().wins();
    expect(w.wins).toHaveLength(5);
    expect(w.unseen).toBe(0);
    expect(w.wins.every((x) => !x.unseen)).toBe(true);
    expect(w.wins.filter((x) => x.kind === 'interview').map((x) => x.company.name)).toEqual(['Distyl AI']);
    expect(w.wins.filter((x) => x.kind === 'referred')).toHaveLength(1);
    expect(w.wins.filter((x) => x.kind === 'replied')).toHaveLength(3);
    expect(w.wins.filter((x) => x.kind === 'replied').every((x) => x.replyKind === 'interested')).toBe(true);
    expect(w.counts).toEqual({ accepted: 0, replied: 3, referred: 1, interview: 1, offer: 0 });
    expect(w.wins.map((x) => x.at)).toEqual([...w.wins.map((x) => x.at)].sort().reverse());
    w.wins.forEach((x) => expect(x.line.length).toBeGreaterThan(0));
  });

  it('findings for October: two, with a changed and a caution', async () => {
    const f = await client().findings();
    expect(f.month).toBe('2026-10-01');
    expect(f.findings).toHaveLength(2);
    const [a, b] = f.findings;
    expect(a?.counts.map((c) => [c.count, c.of])).toEqual([[14, 33], [5, 24]]);
    expect(a?.changed).not.toBeNull();
    expect(b?.counts.map((c) => [c.count, c.of])).toEqual([[5, 71], [0, 29]]);
    expect(b?.caution).not.toBeNull();
  });

  it('watch has one item of each kind', async () => {
    const w = await client().watchList();
    expect(w.items.map((i) => i.kind).sort()).toEqual(['careers_changed', 'merge_candidate', 'new_reason_no_role']);
  });
});

describe('progress fixture', () => {
  it('progress stages are in funnel order with their denominators', async () => {
    const p = await client().progress();
    expect(p.week.stages.map((s) => s.stage)).toEqual(['requests', 'accepts', 'replies', 'referrals', 'interviews']);
    expect(counts(p.week)).toEqual({ requests: 31, accepts: 9, replies: 2, referrals: 0, interviews: 1 });
    expect(p.week.stages.map((s) => s.from)).toEqual([
      null, { stage: 'requests', count: 31 }, { stage: 'requests', count: 31 }, { stage: 'replies', count: 2 }, { stage: 'replies', count: 2 },
    ]);
    expect(counts(p.month)).toEqual({ requests: 52, accepts: 17, replies: 4, referrals: 1, interviews: 2 });
    expect(p.week.enough).toBe(true);
    expect(p.week.quiet).toBe(false);
    expect(p.month.enough).toBe(true);
    expect(p.base).toMatchObject({ requests: 142, accepts: 41, replies: 9, medianDaysToReply: 6, enough: true, minRequests: 30 });
    expect(p.today).toEqual({ planned: 7, done: 2, requests: 2, applications: 2, accepted: 0, replied: 0 });
    expect(p.week.effort.length).toBeGreaterThan(0);
    for (const period of [p.week, p.month]) {
      for (const s of period.stages) if (s.from) expect(period.stages.find((x) => x.stage === s.from?.stage)?.count).toBe(s.from.count);
    }
  });

  it('first_week has too little data: counts only, enough false', async () => {
    const p = await client('first_week').progress();
    expect(p.week.enough).toBe(false);
    expect(p.week.stages[0]?.count).toBe(9);
    expect(p.base.enough).toBe(false);
    expect(p.base.requests).toBeLessThan(p.base.minRequests);
  });

  it('quiet_week has a quiet week, with effort, inside a month that is not quiet', async () => {
    const p = await client('quiet_week').progress();
    expect(p.week.quiet).toBe(true);
    expect(p.week.stages.find((s) => s.stage === 'replies')?.count).toBe(0);
    expect(p.week.effort.some((e) => e.count > 0)).toBe(true);
    expect(p.month.quiet).toBe(false);
    expect(p.base.enough).toBe(true);
  });

  it('fresh has every count at 0 and no base rate', async () => {
    const c = client('fresh');
    const p = await c.progress();
    for (const period of [p.week, p.month]) {
      expect(period.stages.every((s) => s.count === 0)).toBe(true);
      expect(period.effort.every((e) => e.count === 0)).toBe(true);
      expect(period.enough).toBe(false);
      expect(period.quiet).toBe(false);
    }
    expect(p.base).toMatchObject({ requests: 0, accepts: 0, replies: 0, medianDaysToReply: null, enough: false });
    expect(Object.values(p.today).slice(1).every((n) => n === 0)).toBe(true);
    expect((await c.wins()).wins).toEqual([]);
    expect((await c.outcomePrompt()).touches).toEqual([]);
  });
});

describe('plan scenarios', () => {
  it('win has one unseen accept from Dana Whitfield', async () => {
    const w = await client('win').wins();
    expect(w.unseen).toBe(1);
    const unseen = w.wins.filter((x) => x.unseen);
    expect(unseen).toHaveLength(1);
    expect(unseen[0]).toMatchObject({ kind: 'accepted', person: { name: 'Dana Whitfield' }, company: { name: 'Resend' } });
    expect(w.counts.accepted).toBe(1);
    expect((await client('win').progress()).today.accepted).toBe(1);
  });

  it("today's win counts do not depend on whether the wins are seen", () => {
    const db = buildFixture('win');
    expect(db.progress.today.accepted).toBe(1);
    const seen = db.wins.wins.map((w) => ({ ...w, unseen: false }));
    expect(winsOnDay(seen, db.plan.date, 'accepted')).toBe(db.progress.today.accepted);
    expect(winsOnDay(seen, db.plan.date, 'replied')).toBe(db.progress.today.replied);
    // Kept wins from earlier days are not today's.
    expect(db.wins.wins.filter((w) => !w.unseen).length).toBe(5);
    expect(db.progress.today.replied).toBe(0);
  });

  it('plan_done: every row done, with Wen Zhao on row 5', async () => {
    const c = client('plan_done');
    const plan = await c.plan();
    expect(plan.rows.every((r) => r.state === 'done')).toBe(true);
    expect(plan.done).toBe(plan.total);
    expect(plan.total).toBe(7);
    const five = row(plan, 5);
    expect(five.person?.person.name).toBe('Wen Zhao');
    expect(five.findSomeone).toBeNull();
    expect(five.steps.every((s) => s.touchId !== null && s.at !== null)).toBe(true);
    const linkedin = plan.rows.filter((r) => r.channel === 'linkedin_request');
    expect(linkedin).toHaveLength(6);
    expect(linkedin.filter((r) => r.steps.find((s) => s.kind === 'request')?.at).length).toBe(6);
    expect(linkedin.filter((r) => r.steps.find((s) => s.kind === 'apply')?.at).length).toBe(6);
    expect(plan.rows.filter((r) => r.channel === 'email' && r.state === 'done')).toHaveLength(1);
    const p = await c.progress();
    expect(p.today).toMatchObject({ planned: 7, done: 7, requests: 6, applications: 6 });
    expect((await c.shell()).plan).toEqual({ done: 7, total: 7 });
  });

  for (const scenario of ['weekend', 'job_failed'] as const) {
    it(`${scenario}: no rows, notReady with its reason, and a preview of tomorrow`, async () => {
      const c = client(scenario);
      const plan = await c.plan();
      expect(plan.rows).toEqual([]);
      expect(plan.notReady?.reason).toBe(scenario === 'weekend' ? 'weekend' : 'job_failed');
      expect(plan.notReady?.text).not.toBe('');
      expect(plan.tomorrow.total).toBeGreaterThan(0);
      expect(plan.done).toBe(0);
      expect(plan.total).toBe(0);
      expect((await c.shell()).plan).toEqual({ done: 0, total: 0 });
    });
  }

  it('job_failed carries the failed list job and weekend moves the clock to Saturday', async () => {
    expect((await client('job_failed').plan()).listJob?.status).toBe('failed');
    const weekend = await client('weekend').plan();
    expect(weekend.date).toBe('2026-11-14');
    expect(weekend.asOf).toBe('2026-11-14T15:14:00Z');
  });

  it('empty has nothing to plan, win or score', async () => {
    const c = client('empty');
    expect((await c.plan()).rows).toEqual([]);
    expect((await c.wins()).wins).toEqual([]);
    expect((await c.watchList()).items).toEqual([]);
    expect((await c.findings()).findings).toEqual([]);
    await expect(c.score(512)).rejects.toMatchObject({ code: 'not_found' });
  });

  it('no em or en dash in any string of any scenario', () => {
    const bad = /[\u2013\u2014]/;
    const found: string[] = [];
    const walk = (node: unknown, path: string) => {
      if (typeof node === 'string') {
        if (bad.test(node)) found.push(`${path}: ${node}`);
      } else if (Array.isArray(node)) node.forEach((n, i) => walk(n, `${path}[${i}]`));
      else if (node && typeof node === 'object') for (const [k, v] of Object.entries(node)) walk(v, `${path}.${k}`);
    };
    for (const s of SCENARIOS) {
      const db = buildFixture(s);
      walk({ shell: db.shell, plan: db.plan, prompt: db.prompt, wins: db.wins, progress: db.progress, findings: db.findings, watch: db.watch, scores: db.scores }, s);
    }
    expect(found).toEqual([]);
  });
});

describe('score fixture', () => {
  const pylon = async (): Promise<ScoreView> => {
    const c = client();
    const id = (await c.searchIndex()).companies.find((x) => x.name === 'Pylon')?.id;
    if (id === undefined) throw new Error('No Pylon');
    return c.score(id);
  };

  it('score points add up', async () => {
    const s = await pylon();
    expect(s.signals).toHaveLength(8);
    expect(Math.abs(sum(s.signals.map((x) => x.points)) - s.score)).toBeLessThan(0.001);
    expect(Math.abs(sum(s.signals.map((x) => Math.abs(x.share))) - 1)).toBeLessThan(0.001);
    s.signals.forEach((x) => {
      expect(x.reason.length).toBeGreaterThan(0);
      expect(Math.abs(x.value * x.weight - x.points)).toBeLessThan(0.001);
    });
    const size = s.signals.find((x) => x.name === 'size');
    expect(size).toMatchObject({ neutral: true, value: 0 });
    expect(s.signals.filter((x) => x.neutral)).toHaveLength(1);
    expect(s.signals.filter((x) => x.points < 0)).toHaveLength(1);
    expect(s.signals.some((x) => x.source !== null)).toBe(true);
    expect(s.companyId).toBeGreaterThan(0);
    expect(s.lanes.length).toBeGreaterThan(0);
  });

  it('every fixture company has a score that adds up', async () => {
    const c = client();
    const index = await c.searchIndex();
    expect(index.companies.length).toBeGreaterThan(50);
    for (const co of index.companies) {
      const s = await c.score(co.id);
      expect(s.companyId).toBe(co.id);
      expect(Math.abs(sum(s.signals.map((x) => x.points)) - s.score)).toBeLessThan(0.001);
      expect(Math.abs(sum(s.signals.map((x) => Math.abs(x.share))) - 1)).toBeLessThan(0.001);
      s.signals.forEach((x) => expect(x.reason.length).toBeGreaterThan(0));
    }
  });
});
