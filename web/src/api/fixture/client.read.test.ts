import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { ApiError } from '../client';
import { createClient } from '../index';
import type { Id, OutboxItem } from '../types';
import { FixtureClient } from './client';
import { buildFixture, ids, type Scenario } from './data';

const client = (scenario: Scenario = 'default') => new FixtureClient(buildFixture(scenario));

async function itemFor(c: FixtureClient, personName: string): Promise<OutboxItem> {
  const view = await c.outbox();
  const found = view.items.find((i) => i.person.name === personName);
  if (!found) throw new Error(`No outbox item for ${personName}`);
  return found;
}

async function companyIdByName(c: FixtureClient, name: string): Promise<Id> {
  const index = await c.searchIndex();
  const found = index.companies.find((co) => co.name === name);
  if (!found) throw new Error(`No company ${name}`);
  return found.id;
}

function mockupBodies(): string[] {
  const html = readFileSync(resolve(process.cwd(), '../docs/design/outbox.html'), 'utf8');
  return [...html.matchAll(/<textarea[^>]*>([\s\S]*?)<\/textarea>/g)].map((m) => m[1] ?? '');
}

const words = (body: string) => body.split(/\s+/).filter((w) => w && !/^https?:/.test(w)).length;
const links = (body: string) => (body.match(/https?:\/\/\S+/g) ?? []).length;

describe('FixtureClient reads: default scenario', () => {
  it('shell matches the sidebar in the mockups', async () => {
    const s = await client().shell();
    expect(s).toMatchObject({
      asOf: '2026-11-10T15:14:00Z',
      pickedToday: 3,
      capacityToday: 5,
      unhandledReplies: 1,
      draftsToReview: 7,
      linkedinToReach: 12,
      openReviewItems: 5,
      noGoCount: 31,
      doneCount: 4,
      sending: { state: 'running', sentToday: 20, limitToday: 30 },
      repliesThisMonth: 9,
    });
  });

  it('shell.jobs has the crawl run for the sidebar and one late run', async () => {
    const s = await client().shell();
    const crawl = s.jobs.find((j) => j.job === 'crawl');
    expect(crawl?.startedAt).toBe('2026-11-09T13:04:00Z');
    expect(crawl?.late).toBe(false);
    expect(s.jobs.filter((j) => j.late)).toHaveLength(1);
    expect(s.jobs.some((j) => j.status === 'failed')).toBe(false);
  });

  it('today lists 18 proposed in two groups, 5 still emailing and 41 backlog', async () => {
    const t = await client().today();
    expect(t.proposed).toHaveLength(18);
    expect(t.proposed.slice(0, 11).every((e) => e.listGroup === 'level_role')).toBe(true);
    expect(t.proposed.slice(11).every((e) => e.listGroup === 'any_us_role')).toBe(true);
    expect(t.proposed.map((e) => e.rank)).toEqual(Array.from({ length: 18 }, (_, i) => i + 1));
    const picked = t.proposed.filter((e) => e.state === 'picked').map((e) => e.company.name);
    expect(picked).toEqual(['Resend', 'Cerebrium', 'Trigger.dev']);
    expect(t.picked).toBe(3);
    expect(t.capacity).toBe(5);
    expect(t.stillEmailing).toHaveLength(5);
    expect(t.stillEmailing.find((s) => s.company.name === 'Browserbase')?.state).toBe('held');
    expect(t.backlog).toHaveLength(41);
    expect(t.backlog.every((e) => e.state === 'backlog')).toBe(true);
    expect(t.notReady).toBeNull();
  });

  it('today carries what the later views assert', async () => {
    const t = await client().today();
    const by = (name: string) => {
      const e = t.proposed.find((p) => p.company.name === name);
      if (!e) throw new Error(name);
      return e;
    };
    const resend = by('Resend');
    expect(resend.role?.title).toBe('Software Engineer, New Grad');
    expect(resend.size?.value).toBe('38');
    expect(resend.sponsorship).toBe('has_sponsored');
    expect(resend.sponsorshipFact?.value).toBe('Has sponsored H-1B');
    expect(resend.peopleCount).toBe(2);
    expect(by('Trigger.dev').size).toBeNull();
    expect(by('Hex').role?.start2027).toBe(true);
    expect(by('Hex').sponsorship).toBe('unclear');
    expect(by('Turso').size?.confidence).toBe('guess');
    expect(by('Cerebrium').role?.title).toBe('Member of Technical Staff');
    expect(by('Inngest').role?.yearsAsked).toBe(2);
    expect(t.listJob?.status).toBe('ok');
  });

  it('outbox has the sending strip, counts and rows', async () => {
    const o = await client().outbox();
    expect(o.counts).toEqual({ replied: 1, review: 7, approved: 3, sending: 1, checking: 1, sent: 64, paused: 2, closed: 17 });
    expect(o.closedBreakdown).toEqual({ bounced: 1, skipped: 4, companyClosed: 12 });
    expect(o.sending.rampLevel).toBe(2);
    expect(o.sending.rampLevels).toEqual([20, 30, 40, 50, 60, 70, 80, 90, 100]);
    expect(o.sending.nextLimitIfClean).toBe(40);
    expect(o.sending.nextSend).toMatchObject({
      at: '2026-11-10T15:42:00Z',
      tz: 'America/New_York',
      personName: 'Jordan Price',
      companyName: 'Mintlify',
    });
    for (const group of Object.keys(o.counts) as (keyof typeof o.counts)[]) {
      expect(o.items.filter((i) => i.group === group)).toHaveLength(o.counts[group]);
    }
    const order = o.items.map((i) => i.sortKey);
    expect(order).toEqual([...order].sort());
  });

  it('a drafted item carries its projected schedule', async () => {
    const c = client();
    const omar = await itemFor(c, 'Omar Haddad');
    expect(omar.state).toBe('drafted');
    expect(omar.schedule?.text).toBe('Sends after 10:55 ET');
    const dana = await itemFor(c, 'Dana Whitcomb');
    expect(dana.schedule?.text).toBe('Sends Wed Nov 11');
  });

  it('outboxDetail for Kenji has one block with a compare fix and the verbatim body', async () => {
    const c = client();
    const kenji = await itemFor(c, 'Kenji Watanabe');
    expect(kenji.touchId).toBe(ids.touch.kenji);
    const d = await c.outboxDetail(kenji.touchId);
    const blocks = d.checks.filter((k) => k.severity === 'block');
    expect(blocks).toHaveLength(1);
    expect(blocks[0]?.fix?.kind).toBe('compare');
    expect(blocks[0]?.title).toBe("Near-identical to Ava Moreno's draft (92% the same)");
    expect(d.checks.filter((k) => k.severity === 'pass')).toHaveLength(9);
    expect(d.body).toBe(mockupBodies()[0]);
    expect(words(d.body)).toBe(91);
    expect(links(d.body)).toBe(2);
    expect(d.item.person.email).toBe('kenji@trigger.example');
  });

  it('the four sample bodies are copied verbatim from the mockup', async () => {
    const c = client();
    const bodies = mockupBodies();
    const names = ['Kenji Watanabe', 'Dana Whitcomb', 'Marcus Feld', 'Omar Haddad'];
    for (const [i, name] of names.entries()) {
      const item = await itemFor(c, name);
      expect((await c.outboxDetail(item.touchId)).body).toBe(bodies[i]);
    }
  });

  it('outboxDetail for Priya has the reply, the held pair and the Gmail link', async () => {
    const c = client();
    const priya = await itemFor(c, 'Priya Natarajan');
    const d = await c.outboxDetail(priya.touchId);
    expect(d.reply?.text.startsWith('Hi Devanshu')).toBe(true);
    expect(d.reply?.kind).toBe('interested');
    expect(d.held.map((h) => h.personName)).toEqual(['Lena Szabo', 'Tom Becker']);
    expect(d.gmailThreadUrl).toBeTruthy();
    expect(d.thread.map((m) => m.direction)).toEqual(['out', 'out', 'in']);
    expect(d.thread[0]?.at).toBe('2026-10-30T17:02:00Z');
  });

  it('Dana has a recruiter warning whose fix is the role kind', async () => {
    const c = client();
    const dana = await itemFor(c, 'Dana Whitcomb');
    const d = await c.outboxDetail(dana.touchId);
    expect(d.item.person.roleKind).toBe('recruiter');
    expect(d.checks.find((k) => k.severity === 'warn')?.fix).toEqual({ kind: 'role_kind' });
  });

  it('every outbox item has a detail and compareDrafts finds Kenji against Ava', async () => {
    const c = client();
    const o = await c.outbox();
    const samples = [o.items[0], o.items[10], o.items[30], o.items[o.items.length - 1]];
    for (const item of samples) {
      expect((await c.outboxDetail(item!.touchId)).item.touchId).toBe(item!.touchId);
    }
    const kenji = await itemFor(c, 'Kenji Watanabe');
    const ava = await itemFor(c, 'Ava Moreno');
    const cmp = await c.compareDrafts(kenji.messageId, ava.messageId);
    expect(cmp.similarity).toBeCloseTo(0.92);
    expect(cmp.a.personName).toBe('Kenji Watanabe');
    expect(cmp.b.personName).toBe('Ava Moreno');
    expect(cmp.matches.length).toBeGreaterThan(0);
    await expect(c.outboxDetail(999999)).rejects.toMatchObject({ code: 'not_found' });
  });

  it('needsYou lists five open items in view order, Zed waiting 11 days', async () => {
    const n = await client().needsYou();
    expect(n.open.map((i) => i.kind)).toEqual(['domain_missing', 'size_unknown', 'sponsorship_unclear', 'board_unreadable', 'board_empty']);
    expect(n.open.filter((i) => i.holdsEmail).map((i) => i.company.name)).toEqual(['Cerebrium', 'Trigger.dev']);
    expect(n.open.find((i) => i.company.name === 'Zed Industries')?.ageDays).toBe(11);
    expect(n.open.find((i) => i.company.name === 'Hex')?.quote?.text).toContain('visa sponsorship');
    expect(n.resolvedThisWeek).toHaveLength(6);
    expect(n.boardsJob?.job).toBe('crawl');
  });

  it('noGo has 24 companies and 7 people', async () => {
    const n = await client().noGo();
    expect(n.companies).toHaveLength(24);
    const rows = n.companies.filter((r) => r.kind === 'no_go');
    expect(rows).toHaveLength(19);
    expect(rows.filter((r) => r.reason === 'no_sponsorship')).toHaveLength(9);
    expect(rows.filter((r) => r.reason === 'citizenship')).toHaveLength(4);
    expect(rows.filter((r) => r.reason === 'over_300')).toHaveLength(6);
    expect(n.companies.filter((r) => r.kind === 'removed')).toHaveLength(5);
    expect(n.countsByReason).toEqual([
      { reason: 'no_sponsorship', count: 9 },
      { reason: 'citizenship', count: 4 },
      { reason: 'over_300', count: 6 },
      { reason: 'removed', count: 5 },
    ]);
    expect(n.people).toHaveLength(7);
    const ruth = n.people.find((p) => p.target.kind === 'person' && p.target.person.name === 'Ruth Okonkwo');
    expect(ruth?.reason).toBe('stop_reply');
    expect(rows.some((r) => r.importSource !== null)).toBe(true);
  });

  it('company(Browserbase) has 4 postings, 9 people and 31 events across every import source', async () => {
    const c = client();
    const v = await c.company(await companyIdByName(c, 'Browserbase'));
    expect(v.company.name).toBe('Browserbase');
    expect(v.company.day).toBe(3);
    expect(v.company.heldByTouchId).not.toBeNull();
    expect(v.postings).toHaveLength(4);
    expect(v.people).toHaveLength(9);
    expect(v.touches).toHaveLength(7);
    expect(v.evidence).toHaveLength(8);
    expect(v.events).toHaveLength(31);
    const sources = new Set(v.events.map((e) => e.importSource).filter(Boolean));
    expect(sources).toEqual(new Set(['data_js', 'contacted_txt', 'devboard', 'radar_db']));
    const actors = new Set(v.events.map((e) => e.actor));
    for (const a of ['devanshu', 'claude', 'sender', 'crawler', 'import'] as const) expect(actors.has(a)).toBe(true);
    expect(v.facts.find((f) => f.fact === 'size')?.value).toBe('48');
    expect(v.facts.some((f) => f.fact === 'everify')).toBe(true);
    expect(v.sponsorshipShown).toBe('says_yes');
    expect(v.rulesetVersion).toBe('2026.11.02');
  });

  it('every company in the search index has a company page', async () => {
    const c = client();
    const index = await c.searchIndex();
    expect(index.companies.length).toBeGreaterThan(90);
    for (const co of [index.companies[0], index.companies[40], index.companies[index.companies.length - 1]]) {
      expect((await c.company(co!.id)).company.id).toBe(co!.id);
    }
    await expect(c.company(1)).rejects.toMatchObject({ code: 'not_found' });
  });

  it('search finds a posting by initials and names Browserbase first among br', async () => {
    const c = client();
    const hits = await c.searchPostings('mts');
    expect(hits.map((h) => `${h.companyName}: ${h.title}`)).toContain('Cerebrium: Member of Technical Staff');
    expect(await c.searchPostings('zzzz')).toEqual([]);
    const index = await c.searchIndex();
    const browserbase = index.companies.find((co) => co.name === 'Browserbase');
    expect(browserbase?.inProgress).toBe(true);
    expect(browserbase?.statusLine).toBe('Held: reply from Priya Natarajan');
    expect(index.people.some((p) => p.name === 'Lena Szabo' && p.companyName === 'Browserbase')).toBe(true);
  });

  it('linkedin has the tab counts, Mintlify and the Browserbase hold', async () => {
    const l = await client().linkedin();
    expect(l.counts).toEqual({ toReach: 12, waiting: 9, replied: 2, all: 31 });
    expect(l.requestsThisWeek).toBe(14);
    const people = l.groups.flatMap((g) => g.people);
    expect(people.filter((p) => p.tab === 'to_reach')).toHaveLength(12);
    expect(people.filter((p) => p.tab === 'waiting')).toHaveLength(9);
    expect(people.filter((p) => p.tab === 'replied')).toHaveLength(2);
    const mintlify = l.groups.find((g) => g.company.name === 'Mintlify');
    expect(mintlify?.role?.title).toBe('Software Engineer, New Grad');
    expect(mintlify).toMatchObject({ emailed: 8, onLinkedIn: 4 });
    expect(mintlify?.people.find((p) => p.person.name === 'Hannah Cole')).toMatchObject({ tab: 'to_reach', steps: [] });
    expect(mintlify?.people.find((p) => p.person.name === 'Luis Romero')?.steps[0]).toEqual({ step: 'request_sent', at: '2026-11-06T17:00:00Z' });
    const browserbase = l.groups.find((g) => g.company.name === 'Browserbase');
    expect(browserbase?.heldBy?.personName).toBe('Priya Natarajan');
    expect(l.groups.every((g) => g.onLinkedIn === g.people.length)).toBe(true);
  });

  it('done lists four companies and eight weeks of replies', async () => {
    const d = await client().done();
    expect(d.companies.map((c) => c.company.name)).toEqual(['Crusoe', 'Retell AI', 'Distyl AI', 'Northbeam Labs']);
    expect(d.companies.map((c) => c.imported)).toEqual([true, true, true, false]);
    expect(d.metrics).toMatchObject({ repliesThisMonth: 9, replyRate: { replies: 9, firstEmails: 140 }, referrals: 2, interviews: 3 });
    expect(d.metrics.repliesPerWeek).toHaveLength(8);
    expect(d.metrics.repliesPerWeek.map((w) => w.count)).toEqual([0, 1, 2, 1, 3, 4, 5, 4]);
    expect(d.metrics.repliesPerWeek.find((w) => w.weekOf === '2026-11-02')?.count).toBe(5);
  });
});

describe('FixtureClient scenarios', () => {
  it('hard_block pauses sending with the Gmail message', async () => {
    const c = client('hard_block');
    const o = await c.outbox();
    expect(o.sending.state).toBe('paused_hard_block');
    expect(o.sending.gmailMessage).toBeTruthy();
    expect(o.sending.nextSend).toBeNull();
    expect((await c.shell()).sending.state).toBe('paused_hard_block');
  });

  it('signed_out says so', async () => {
    const c = client('signed_out');
    expect((await c.outbox()).sending.state).toBe('signed_out');
    expect((await c.shell()).sending.state).toBe('signed_out');
  });

  it('weekend has no list', async () => {
    const c = client('weekend');
    const t = await c.today();
    expect(t.notReady?.reason).toBe('weekend');
    expect(t.notReady?.text).toBe('No list on weekends. The backlog is still here.');
    expect(t.proposed).toEqual([]);
    expect(t.backlog).toHaveLength(41);
    expect(t.asOf).toBe('2026-11-14T15:14:00Z');
    expect((await c.outbox()).sending.state).toBe('weekend');
  });

  it('job_failed explains the missing list', async () => {
    const c = client('job_failed');
    const t = await c.today();
    expect(t.notReady?.reason).toBe('job_failed');
    expect(t.notReady?.text).toBe("Today's list is not ready. It is built at 08:00 on weekdays.");
    expect(t.listJob?.status).toBe('failed');
    expect(t.listJob?.error).not.toBe('');
    expect((await c.shell()).jobs.find((j) => j.job === 'daily_list')?.status).toBe('failed');
  });

  it('empty gives empty lists everywhere', async () => {
    const c = client('empty');
    const s = await c.shell();
    expect(s).toMatchObject({ pickedToday: 0, unhandledReplies: 0, draftsToReview: 0, linkedinToReach: 0, openReviewItems: 0, noGoCount: 0, doneCount: 0, repliesThisMonth: 0 });
    const t = await c.today();
    expect([t.proposed, t.backlog, t.stillEmailing].every((l) => l.length === 0)).toBe(true);
    expect(t.notReady).toBeNull();
    const o = await c.outbox();
    expect(o.items).toEqual([]);
    expect(Object.values(o.counts).every((n) => n === 0)).toBe(true);
    expect((await c.linkedin()).groups).toEqual([]);
    const n = await c.needsYou();
    expect([n.open, n.resolvedThisWeek].every((l) => l.length === 0)).toBe(true);
    const g = await c.noGo();
    expect([g.companies, g.people].every((l) => l.length === 0)).toBe(true);
    const d = await c.done();
    expect(d.companies).toEqual([]);
    expect(d.metrics.repliesPerWeek).toHaveLength(8);
    const index = await c.searchIndex();
    expect([index.companies, index.people].every((l) => l.length === 0)).toBe(true);
    expect(await c.searchPostings('engineer')).toEqual([]);
  });

  it('error rejects every read with a network ApiError', async () => {
    const c = client('error');
    const reads: (() => Promise<unknown>)[] = [
      () => c.shell(), () => c.searchIndex(), () => c.searchPostings('a'), () => c.today(), () => c.outbox(), () => c.outboxDetail(1),
      () => c.compareDrafts(1, 2), () => c.linkedin(), () => c.needsYou(), () => c.noGo(), () => c.company(1), () => c.done(),
      () => c.plan(), () => c.outcomePrompt(), () => c.wins(), () => c.progress(), () => c.findings(), () => c.watchList(), () => c.score(1),
    ];
    for (const read of reads) {
      const err = await read().catch((e: unknown) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).code).toBe('network');
    }
  });

  it('many_sent gives 500 sent items', async () => {
    const o = await client('many_sent').outbox();
    expect(o.items.filter((i) => i.group === 'sent')).toHaveLength(500);
    expect(o.counts.sent).toBe(500);
    expect(o.counts.review).toBe(7);
    const ids = o.items.map((i) => i.touchId);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe('FixtureClient behavior', () => {
  it('has source sample', () => {
    expect(client().source).toBe('sample');
  });

  it('reads return copies, so a caller cannot change the db', async () => {
    const c = client();
    const first = await c.shell();
    first.pickedToday = 99;
    first.sending.limitToday = 1;
    const o = await c.outbox();
    o.items.length = 0;
    expect((await c.shell()).pickedToday).toBe(3);
    expect((await c.shell()).sending.limitToday).toBe(30);
    expect((await c.outbox()).items.length).toBeGreaterThan(0);
  });

  it('the plan reads return copies, so a caller cannot change the db', async () => {
    const c = client();
    const plan = await c.plan();
    plan.rows.length = 0;
    plan.done = 99;
    const wins = await c.wins();
    wins.wins.length = 0;
    const id = (await c.searchIndex()).companies.find((x) => x.name === 'Pylon')?.id ?? 0;
    const score = await c.score(id);
    score.signals.length = 0;
    expect((await c.plan()).rows).toHaveLength(7);
    expect((await c.plan()).done).toBe(2);
    expect((await c.wins()).wins).toHaveLength(5);
    expect((await c.score(id)).signals).toHaveLength(8);
    for (const read of [() => c.outcomePrompt(), () => c.progress(), () => c.findings(), () => c.watchList()]) {
      const first = await read();
      (first as { asOf: string }).asOf = 'changed';
      expect((await read()).asOf).toBe('2026-11-10T15:14:00Z');
    }
  });

  it('score rejects not_found for an unknown company', async () => {
    const err = await client().score(999999).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe('not_found');
  });

  it('buildFixture returns a fresh copy each call', () => {
    const a = buildFixture();
    a.shell.pickedToday = 42;
    expect(buildFixture().shell.pickedToday).toBe(3);
  });

  it('waits latencyMs before answering', async () => {
    vi.useFakeTimers();
    try {
      const c = new FixtureClient(buildFixture(), { latencyMs: 150 });
      let done = false;
      void c.shell().then(() => { done = true; });
      await vi.advanceTimersByTimeAsync(149);
      expect(done).toBe(false);
      await vi.advanceTimersByTimeAsync(1);
      expect(done).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it('createClient reads ?scenario= and falls back to default', async () => {
    // A promise: the fixture client and its data load as their own chunk, not in the main bundle.
    expect(createClient('')).toBeInstanceOf(Promise);
    const hard = await createClient('?scenario=hard_block');
    expect(hard.source).toBe('sample');
    expect((await hard.shell()).sending.state).toBe('paused_hard_block');
    expect((await (await createClient('?scenario=nope')).shell()).sending.state).toBe('running');
    expect((await (await createClient('')).shell()).sending.state).toBe('running');
  });
});
