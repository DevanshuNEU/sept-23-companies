import { ApiError, type WriteOptions } from '../client';
import type { Check, DailyEntry, Id, OutboxDetail, OutboxItem, PlanRow } from '../types';
import { FixtureClient } from './client';
import { buildFixture, companyId, ids, type Scenario } from './data';

const EM_DASH = String.fromCharCode(0x2014);
const EN_DASH = String.fromCharCode(0x2013);

const client = (scenario: Scenario = 'default') => new FixtureClient(buildFixture(scenario));

let counter = 0;
const opt = (): WriteOptions => ({ requestId: `req-${++counter}` });

async function itemFor(c: FixtureClient, personName: string): Promise<OutboxItem> {
  const found = (await c.outbox()).items.find((i) => i.person.name === personName);
  if (!found) throw new Error(`No outbox item for ${personName}`);
  return found;
}

async function entryFor(c: FixtureClient, name: string): Promise<DailyEntry> {
  const today = await c.today();
  const found = [...today.proposed, ...today.backlog].find((e) => e.company.name === name);
  if (!found) throw new Error(`No today entry for ${name}`);
  return found;
}

async function rejection(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p;
  } catch (e) {
    if (e instanceof ApiError) return e;
    throw e;
  }
  throw new Error('Expected the call to reject');
}

const detailOf = (c: FixtureClient, item: OutboxItem): Promise<OutboxDetail> => c.outboxDetail(item.touchId);
const sortedGroup = async (c: FixtureClient, group: OutboxItem['group']) =>
  (await c.outbox()).items.filter((i) => i.group === group).sort((a, b) => (a.sortKey < b.sortKey ? -1 : 1)).map((i) => i.person.name);

describe('outbox writes', () => {
  it('approve refuses a blocked draft', async () => {
    const c = client();
    const kenji = await itemFor(c, 'Kenji Watanabe');
    const err = await rejection(c.approve(kenji.messageId, kenji.revision, opt()));
    expect(err.code).toBe('blocked');
    expect(err.checks.map((k) => k.code)).toEqual(['near_identical']);
    expect((await detailOf(c, kenji)).item.state).toBe('drafted');
    expect((await itemFor(c, 'Kenji Watanabe')).revision).toBe(kenji.revision);
  });

  it('approve moves a clean draft to approved', async () => {
    const c = client();
    const omar = await itemFor(c, 'Omar Haddad');
    const detail = await c.approve(omar.messageId, omar.revision, opt());
    expect(detail.item).toMatchObject({ state: 'approved', group: 'approved', revision: omar.revision + 1 });
    expect(detail.item.schedule?.text).toBe('Sends after 10:55 ET');
    const after = await itemFor(c, 'Omar Haddad');
    expect(after).toMatchObject({ state: 'approved', group: 'approved', revision: omar.revision + 1 });
    const outbox = await c.outbox();
    expect(outbox.counts).toMatchObject({ review: 6, approved: 4 });
    expect((await c.shell()).draftsToReview).toBe(6);
  });

  it('same requestId twice approves once', async () => {
    const c = client();
    const omar = await itemFor(c, 'Omar Haddad');
    const o = opt();
    const first = await c.approve(omar.messageId, omar.revision, o);
    const second = await c.approve(omar.messageId, omar.revision, o);
    expect(second).toEqual(first);
    expect((await itemFor(c, 'Omar Haddad')).revision).toBe(omar.revision + 1);
    expect((await c.outbox()).counts.approved).toBe(4);
  });

  it('stale revision is a conflict', async () => {
    const c = client();
    const omar = await itemFor(c, 'Omar Haddad');
    const detail = await detailOf(c, omar);
    const err = await rejection(c.saveMessage(omar.messageId, { subject: omar.subject, body: detail.body, revision: 0 }, opt()));
    expect(err.code).toBe('conflict');
    expect((await rejection(c.approve(omar.messageId, 0, opt()))).code).toBe('conflict');
    expect((await detailOf(c, omar)).body).toBe(detail.body);
  });

  it('approve is refused when the draft is not a draft any more', async () => {
    const c = client();
    const jordan = await itemFor(c, 'Jordan Price');
    expect((await rejection(c.approve(jordan.messageId, jordan.revision, opt()))).code).toBe('not_allowed');
  });

  describe('save updates checks', () => {
    it('a changed middle paragraph clears the near-identical block', async () => {
      const c = client();
      const kenji = await itemFor(c, 'Kenji Watanabe');
      const detail = await detailOf(c, kenji);
      const body = detail.body.replace('I graduated from Northeastern in May 2026, and I want in at Trigger.', 'I finished at Northeastern in May 2026 and want to build durable workflows at Trigger.');
      expect(body).not.toBe(detail.body);
      const saved = await c.saveMessage(kenji.messageId, { subject: kenji.subject, body, revision: kenji.revision }, opt());
      expect(saved.checks.some((k) => k.severity === 'block')).toBe(false);
      expect(saved.body).toBe(body);
      expect(saved).toMatchObject({ savedBy: 'devanshu' });
      expect(saved.item).toMatchObject({ blockCount: 0, worst: 'pass', revision: kenji.revision + 1, summary: 'All checks passed' });
      const row = await itemFor(c, 'Kenji Watanabe');
      expect(row).toMatchObject({ blockCount: 0, worst: 'pass', revision: kenji.revision + 1 });
      await expect(c.approve(row.messageId, row.revision, opt())).resolves.toMatchObject({ item: { state: 'approved' } });
    });

    it('an edit outside the middle keeps the block', async () => {
      const c = client();
      const kenji = await itemFor(c, 'Kenji Watanabe');
      const detail = await detailOf(c, kenji);
      const saved = await c.saveMessage(kenji.messageId, { subject: 'your post on safe retries', body: detail.body.replace('Hi Kenji,', 'Hello Kenji,'), revision: kenji.revision }, opt());
      expect(saved.checks.filter((k) => k.severity === 'block')).toHaveLength(1);
      expect(saved.item).toMatchObject({ blockCount: 1, worst: 'block', subject: 'your post on safe retries' });
      expect((await itemFor(c, 'Kenji Watanabe')).subject).toBe('your post on safe retries');
    });

    it('"I\'d love to" adds a banned phrase warning with its span, and removing it brings the pass back', async () => {
      const c = client();
      const omar = await itemFor(c, 'Omar Haddad');
      const detail = await detailOf(c, omar);
      const body = detail.body.replace('Could I pick', "I'd love to help. Could I pick");
      const saved = await c.saveMessage(omar.messageId, { subject: omar.subject, body, revision: omar.revision }, opt());
      const warn = saved.checks.find((k): k is Check => k.title === 'Banned phrase');
      expect(warn?.severity).toBe('warn');
      expect(warn?.spans).toHaveLength(1);
      const span = warn?.spans[0];
      expect(span?.field).toBe('body');
      expect(body.slice(span?.start, span?.end)).toBe("I'd love to");
      expect(saved.checks.some((k) => k.title === 'No banned phrases')).toBe(false);
      expect(saved.item).toMatchObject({ warnCount: 1, worst: 'warn', summary: 'Banned phrase' });
      expect(saved.checks.findIndex((k) => k.title === 'Banned phrase')).toBeLessThan(saved.checks.findIndex((k) => k.severity === 'pass'));

      const again = await c.saveMessage(omar.messageId, { subject: omar.subject, body: detail.body, revision: saved.item.revision }, opt());
      expect(again.checks.some((k) => k.title === 'Banned phrase')).toBe(false);
      expect(again.checks.some((k) => k.title === 'No banned phrases')).toBe(true);
      expect(again.item).toMatchObject({ warnCount: 0, worst: 'pass', summary: 'All checks passed' });
    });

    it('an em dash adds a warning with its span', async () => {
      const c = client();
      const omar = await itemFor(c, 'Omar Haddad');
      const detail = await detailOf(c, omar);
      const body = detail.body.replace('steady rate.', `steady rate ${EM_DASH} always.`);
      const saved = await c.saveMessage(omar.messageId, { subject: omar.subject, body, revision: omar.revision }, opt());
      const warn = saved.checks.find((k) => k.severity === 'warn');
      expect(warn?.title).toMatch(/dash/i);
      expect(warn?.spans).toEqual([{ field: 'body', start: body.indexOf(EM_DASH), end: body.indexOf(EM_DASH) + 1 }]);
      expect(saved.checks.some((k) => k.title === 'No em or en dashes')).toBe(false);
      expect(saved.item.warnCount).toBe(1);

      const en = await c.saveMessage(omar.messageId, { subject: omar.subject, body: detail.body.replace('steady rate.', `steady rate ${EN_DASH} always.`), revision: saved.item.revision }, opt());
      expect(en.checks.filter((k) => k.severity === 'warn')).toHaveLength(1);
    });

    it('only a draft can be saved', async () => {
      const c = client();
      const jordan = await itemFor(c, 'Jordan Price');
      const detail = await detailOf(c, jordan);
      const err = await rejection(c.saveMessage(jordan.messageId, { subject: jordan.subject, body: detail.body, revision: jordan.revision }, opt()));
      expect(err.code).toBe('not_allowed');
    });
  });

  it('unapprove is refused once claimed, and withdraws an approval before that', async () => {
    const c = client();
    const chloe = await itemFor(c, 'Chloe Martin');
    expect((await rejection(c.unapprove(chloe.messageId, chloe.revision, opt()))).code).toBe('not_allowed');
    const ben = await itemFor(c, 'Ben Ito');
    expect((await rejection(c.unapprove(ben.messageId, ben.revision, opt()))).code).toBe('not_allowed');

    const jordan = await itemFor(c, 'Jordan Price');
    const detail = await c.unapprove(jordan.messageId, jordan.revision, opt());
    expect(detail.item).toMatchObject({ state: 'drafted', group: 'review', revision: jordan.revision + 1 });
    expect((await c.shell()).draftsToReview).toBe(8);
    expect((await c.outbox()).counts.approved).toBe(2);
    expect((await rejection(c.unapprove(jordan.messageId, jordan.revision + 1, opt()))).code).toBe('not_allowed');
  });

  it('skip is refused while sending', async () => {
    const c = client();
    for (const name of ['Chloe Martin', 'Ben Ito']) {
      const item = await itemFor(c, name);
      expect((await rejection(c.skip(item.messageId, item.revision, opt()))).code).toBe('not_allowed');
      expect((await itemFor(c, name)).state).toBe(item.state);
    }
  });

  it('skip closes a draft and counts it as skipped', async () => {
    const c = client();
    const sara = await itemFor(c, 'Sara Lindqvist');
    const before = await c.outbox();
    const detail = await c.skip(sara.messageId, sara.revision, opt());
    expect(detail.item).toMatchObject({ state: 'skipped', group: 'closed', touchState: 'closed', revision: sara.revision + 1 });
    const after = await c.outbox();
    expect(after.counts).toMatchObject({ review: before.counts.review - 1, closed: before.counts.closed + 1 });
    expect(after.closedBreakdown.skipped).toBe(before.closedBreakdown.skipped + 1);
  });

  it('approve then unapprove puts the row back where it was', async () => {
    const c = client();
    const reviewBefore = await sortedGroup(c, 'review');
    const approvedBefore = await sortedGroup(c, 'approved');
    const omar = await itemFor(c, 'Omar Haddad');
    await c.approve(omar.messageId, omar.revision, opt());
    expect(await sortedGroup(c, 'approved')).toEqual(['Jordan Price', 'Omar Haddad', 'Arjun Mehta', 'Felix Graham']);
    await c.unapprove(omar.messageId, omar.revision + 1, opt());
    expect(await sortedGroup(c, 'review')).toEqual(reviewBefore);
    expect(await sortedGroup(c, 'approved')).toEqual(approvedBefore);
  });

  it('next send follows the approved queue', async () => {
    const c = client();
    expect((await c.outbox()).sending.nextSend?.personName).toBe('Jordan Price');
    const jordan = await itemFor(c, 'Jordan Price');
    await c.skip(jordan.messageId, jordan.revision, opt());
    expect((await c.outbox()).sending.nextSend?.personName).toBe('Arjun Mehta');
  });

  describe('setReplyKind', () => {
    it('asked_to_stop adds the person to no-go and counts a stop reply', async () => {
      const c = client();
      const priya = await itemFor(c, 'Priya Natarajan');
      const detail = await c.setReplyKind(priya.touchId, 'asked_to_stop', (await detailOf(c, priya)).touchRevision, opt());
      expect(detail.reply?.kind).toBe('asked_to_stop');
      const noGo = await c.noGo();
      const row = noGo.people.find((r) => r.target.kind === 'person' && r.target.person.name === 'Priya Natarajan');
      expect(row).toMatchObject({ kind: 'no_go', reason: 'stop_reply', source: 'reply' });
      expect(noGo.people).toHaveLength(8);
      expect((await c.outbox()).sending.signals.stopRepliesToday).toBe(1);
      expect((await c.shell()).noGoCount).toBe(32);
    });

    it('said_no adds the person to no-go without a stop reply', async () => {
      const c = client();
      const priya = await itemFor(c, 'Priya Natarajan');
      await c.setReplyKind(priya.touchId, 'said_no', (await detailOf(c, priya)).touchRevision, opt());
      const row = (await c.noGo()).people.find((r) => r.target.kind === 'person' && r.target.person.name === 'Priya Natarajan');
      expect(row?.reason).toBe('said_no');
      expect((await c.outbox()).sending.signals.stopRepliesToday).toBe(0);
    });

    it('other kinds only change the kind, and a stale touch revision conflicts', async () => {
      const c = client();
      const priya = await itemFor(c, 'Priya Natarajan');
      const rev = (await detailOf(c, priya)).touchRevision;
      const detail = await c.setReplyKind(priya.touchId, 'not_now', rev, opt());
      expect(detail).toMatchObject({ reply: { kind: 'not_now' }, touchRevision: rev + 1 });
      expect((await c.noGo()).people).toHaveLength(7);
      expect((await rejection(c.setReplyKind(priya.touchId, 'interested', rev, opt()))).code).toBe('conflict');
      expect((await rejection(c.setReplyKind(ids.touch.omar, 'not_now', 1, opt()))).code).toBe('not_allowed');
    });
  });

  it('setRoleKind changes every copy of the person and clears the recruiter warning', async () => {
    const c = client();
    const dana = await itemFor(c, 'Dana Whitcomb');
    expect((await detailOf(c, dana)).checks.some((k) => k.fix?.kind === 'role_kind')).toBe(true);
    expect((await rejection(c.setRoleKind(dana.person.id, 'engineer', 0, opt()))).code).toBe('conflict');
    const person = await c.setRoleKind(dana.person.id, 'engineer', dana.person.revision, opt());
    expect(person).toMatchObject({ id: dana.person.id, roleKind: 'engineer', revision: dana.person.revision + 1 });
    const detail = await detailOf(c, dana);
    expect(detail.item.person.roleKind).toBe('engineer');
    expect(detail.checks.some((k) => k.fix?.kind === 'role_kind')).toBe(false);
    expect((await itemFor(c, 'Dana Whitcomb')).person.roleKind).toBe('engineer');
    expect((await itemFor(c, 'Dana Whitcomb')).warnCount).toBe(0);
  });
});

describe('Today writes', () => {
  it('pick stops at capacity', async () => {
    const c = client();
    for (const name of ['Pylon', 'Baseten']) {
      const e = await entryFor(c, name);
      const picked = await c.pick(e.company.id, e.revision, opt());
      expect(picked).toMatchObject({ state: 'picked', revision: e.revision + 1 });
      expect(picked.company.status).toBe('working');
    }
    expect(await c.today()).toMatchObject({ picked: 5, capacity: 5 });
    expect((await c.shell()).pickedToday).toBe(5);
    const inngest = await entryFor(c, 'Inngest');
    const err = await rejection(c.pick(inngest.company.id, inngest.revision, opt()));
    expect(err.code).toBe('capacity');
    expect((await entryFor(c, 'Inngest')).state).toBe('proposed');
  });

  it('pick with a stale revision is a conflict and picking twice is not allowed', async () => {
    const c = client();
    const pylon = await entryFor(c, 'Pylon');
    expect((await rejection(c.pick(pylon.company.id, 0, opt()))).code).toBe('conflict');
    const picked = await c.pick(pylon.company.id, pylon.revision, opt());
    expect((await rejection(c.pick(pylon.company.id, picked.revision, opt()))).code).toBe('not_allowed');
  });

  it('unpick frees a slot', async () => {
    const c = client();
    const resend = await entryFor(c, 'Resend');
    const entry = await c.unpick(resend.company.id, resend.revision, opt());
    expect(entry.state).toBe('proposed');
    expect((await c.today()).picked).toBe(2);
    expect((await c.shell()).pickedToday).toBe(2);
    expect((await rejection(c.unpick(resend.company.id, entry.revision, opt()))).code).toBe('not_allowed');
  });

  it('backlog moves a proposal out of the list and pick brings it back', async () => {
    const c = client();
    const pylon = await entryFor(c, 'Pylon');
    const moved = await c.backlog(pylon.company.id, pylon.revision, opt());
    expect(moved.state).toBe('backlog');
    const today = await c.today();
    expect(today.proposed.some((e) => e.company.name === 'Pylon')).toBe(false);
    expect(today.backlog[0]?.company.name).toBe('Pylon');
    expect(today.backlog).toHaveLength(42);
    const back = await c.pick(pylon.company.id, moved.revision, opt());
    expect(back.state).toBe('picked');
    const after = await c.today();
    expect(after.backlog).toHaveLength(41);
    expect(after.proposed.find((e) => e.company.name === 'Pylon')?.state).toBe('picked');
  });

  it('removeCompany takes it off Today into the removed list, and restoreCompany puts it back in the backlog', async () => {
    const c = client();
    const pylon = await entryFor(c, 'Pylon');
    await c.removeCompany(pylon.company.id, 'not_a_fit', 'Sells ads', pylon.revision, opt());
    expect((await c.today()).proposed.some((e) => e.company.name === 'Pylon')).toBe(false);
    const noGo = await c.noGo();
    const row = noGo.companies.find((r) => r.kind === 'removed' && r.company.name === 'Pylon');
    expect(row).toMatchObject({ kind: 'removed', removeReason: 'not_a_fit', note: 'Sells ads' });
    expect(noGo.countsByReason.find((r) => r.reason === 'removed')?.count).toBe(6);
    expect((await c.shell()).noGoCount).toBe(32);
    if (row?.kind !== 'removed') throw new Error('expected a removed row');

    await c.restoreCompany(pylon.company.id, 'Changed my mind', row.company.revision, opt());
    expect((await c.noGo()).companies.some((r) => r.kind === 'removed' && r.company.name === 'Pylon')).toBe(false);
    const restored = (await c.today()).backlog.find((e) => e.company.name === 'Pylon');
    expect(restored).toMatchObject({ state: 'backlog', role: pylon.role });
    expect(restored?.company.status).toBe('backlog');
    const view = await c.company(pylon.company.id);
    expect(view.company.status).toBe('backlog');
    expect(restored?.revision).toBe(view.company.revision);
  });

  it('restoreCompany moves a removed company to the backlog', async () => {
    const c = client();
    const noGo = await c.noGo();
    const row = noGo.companies.find((r) => r.kind === 'removed' && r.company.name === 'Brightwater Analytics');
    if (row?.kind !== 'removed') throw new Error('expected a removed row');
    expect((await rejection(c.restoreCompany(row.company.id, 'Back', 0, opt()))).code).toBe('conflict');
    await c.restoreCompany(row.company.id, 'Back', row.company.revision, opt());
    const after = await c.noGo();
    expect(after.companies.some((r) => r.kind === 'removed' && r.company.name === 'Brightwater Analytics')).toBe(false);
    expect(after.countsByReason.find((r) => r.reason === 'removed')?.count).toBe(4);
    const entry = (await c.today()).backlog.find((e) => e.company.name === 'Brightwater Analytics');
    expect(entry).toMatchObject({ state: 'backlog', company: { status: 'backlog' } });
    expect((await c.company(row.company.id)).company.status).toBe('backlog');
    expect((await c.searchIndex()).companies.find((x) => x.name === 'Brightwater Analytics')?.status).toBe('backlog');
    expect((await rejection(c.restoreCompany(row.company.id, 'Again', row.company.revision + 1, opt()))).code).toBe('not_found');
  });
});

describe('company writes', () => {
  it('resumeCompany releases held mail', async () => {
    const c = client();
    const id = companyId('Browserbase');
    const view = await c.company(id);
    expect(view.company.heldByTouchId).toBe(ids.touch.priya);
    expect(await c.resumeCompany(id, view.company.revision, opt())).toEqual({ released: 2 });
    expect((await c.company(id)).company).toMatchObject({ heldByTouchId: null, revision: view.company.revision + 1 });
    const priya = await itemFor(c, 'Priya Natarajan');
    expect(priya.company.heldByTouchId).toBeNull();
    expect((await detailOf(c, priya)).held).toEqual([]);
    expect((await c.today()).stillEmailing.find((s) => s.company.name === 'Browserbase')?.state).toBe('emailing');
    expect((await c.linkedin()).groups.find((g) => g.company.name === 'Browserbase')?.heldBy).toBeNull();
    expect((await rejection(c.resumeCompany(id, view.company.revision + 1, opt()))).code).toBe('not_allowed');
  });

  it('pauseCompany moves drafts to Paused and resumeCompany brings them back', async () => {
    const c = client();
    const id = companyId('Graphite');
    const rev = (await c.company(id)).company.revision;
    await c.pauseCompany(id, rev, opt());
    let outbox = await c.outbox();
    expect(outbox.counts).toMatchObject({ review: 6, approved: 1, paused: 5 });
    expect(outbox.items.find((i) => i.person.name === 'Arjun Mehta')?.group).toBe('paused');
    expect((await c.company(id)).company.pausedAt).not.toBeNull();
    expect((await c.today()).stillEmailing.find((s) => s.company.name === 'Graphite')).toMatchObject({ state: 'paused', nextStep: { kind: 'paused' } });
    expect((await rejection(c.pauseCompany(id, rev + 1, opt()))).code).toBe('not_allowed');

    expect(await c.resumeCompany(id, rev + 1, opt())).toEqual({ released: 3 });
    outbox = await c.outbox();
    expect(outbox.counts).toMatchObject({ review: 7, approved: 3, paused: 2 });
    expect((await c.company(id)).company.pausedAt).toBeNull();
  });

  it('resumeCompany on a company that is not held or paused is refused', async () => {
    const c = client();
    const id = companyId('Supabase');
    expect((await rejection(c.resumeCompany(id, 1, opt()))).code).toBe('not_allowed');
  });

  it('closeCompany no_sponsorship adds a no_go row', async () => {
    const c = client();
    const e = await entryFor(c, 'Pylon');
    await c.closeCompany(e.company.id, 'no_sponsorship', 'They told me on a call', e.revision, opt());
    const noGo = await c.noGo();
    const row = noGo.companies.find((r) => r.kind === 'no_go' && r.target.kind === 'company' && r.target.company.name === 'Pylon');
    expect(row).toMatchObject({ kind: 'no_go', reason: 'no_sponsorship', note: 'They told me on a call', source: 'devanshu' });
    expect(noGo.countsByReason.find((r) => r.reason === 'no_sponsorship')?.count).toBe(10);
    expect((await c.shell()).noGoCount).toBe(32);
    const view = await c.company(e.company.id);
    expect(view.company).toMatchObject({ status: 'closed', closed: { reason: 'no_sponsorship', note: 'They told me on a call' } });
    expect((await c.today()).proposed.some((x) => x.company.name === 'Pylon')).toBe(false);
  });

  it('closeCompany interview lists it in done()', async () => {
    const c = client();
    const id = companyId('Mintlify');
    const before = await c.outbox();
    const rev = (await c.company(id)).company.revision;
    await c.closeCompany(id, 'interview', 'Phone screen booked', rev, opt());
    const done = await c.done();
    expect(done.companies.find((d) => d.company.name === 'Mintlify')).toMatchObject({ outcome: 'interview', outcomeLabel: 'Interviewing', note: 'Phone screen booked', imported: false });
    expect(done.metrics.interviews).toBe(4);
    expect((await c.shell()).doneCount).toBe(5);
    const after = await c.outbox();
    expect(after.counts.closed).toBe(before.counts.closed + 3);
    expect(after.closedBreakdown.companyClosed).toBe(before.closedBreakdown.companyClosed + 3);
    expect(after.items.find((i) => i.person.name === 'Dana Whitcomb')).toMatchObject({ group: 'closed', touchState: 'closed' });
    expect(after.items.find((i) => i.person.name === 'Nina Kovac')?.group).toBe('sent');
    expect((await c.noGo()).companies).toHaveLength(24);
    expect((await rejection(c.closeCompany(id, 'other', '', rev + 1, opt()))).code).toBe('not_allowed');
  });

  it('reopenCompany brings a closed company back', async () => {
    const c = client();
    const id = companyId('Mintlify');
    const rev = (await c.company(id)).company.revision;
    await c.closeCompany(id, 'interview', '', rev, opt());
    await c.reopenCompany(id, rev + 1, opt());
    expect((await c.company(id)).company).toMatchObject({ status: 'working', closed: null });
    expect((await c.done()).companies.some((d) => d.company.name === 'Mintlify')).toBe(false);
    expect((await c.done()).metrics.interviews).toBe(3);
    expect((await c.outbox()).items.find((i) => i.person.name === 'Dana Whitcomb')?.group).toBe('review');
    expect((await rejection(c.reopenCompany(id, rev + 2, opt()))).code).toBe('not_allowed');
  });

  it('reopenCompany leaves a rules no-go alone', async () => {
    const c = client();
    const id = companyId('Halberd Systems');
    const rev = (await c.company(id)).company.revision;
    expect((await rejection(c.reopenCompany(id, rev, opt()))).code).toBe('not_allowed');
  });

  it('liftNoGo removes the row from noGo()', async () => {
    const c = client();
    const noGo = await c.noGo();
    const company = noGo.companies.find((r) => r.kind === 'no_go' && r.target.kind === 'company' && r.target.company.name === 'Halberd Systems');
    const person = noGo.people.find((r) => r.target.kind === 'person' && r.target.person.name === 'Ruth Okonkwo');
    if (company?.kind !== 'no_go' || !person) throw new Error('expected rows');

    await c.liftNoGo(company.noGoId, 'They sponsor now', opt());
    await c.liftNoGo(person.noGoId, 'Wrote back, fine', opt());
    const after = await c.noGo();
    expect(after.companies.some((r) => r.kind === 'no_go' && r.noGoId === company.noGoId)).toBe(false);
    expect(after.people.some((r) => r.noGoId === person.noGoId)).toBe(false);
    expect(after.countsByReason.find((r) => r.reason === 'no_sponsorship')?.count).toBe(8);
    expect((await c.shell()).noGoCount).toBe(29);
    expect((await c.company(company.target.kind === 'company' ? company.target.company.id : 0)).company.status).toBe('candidate');
    expect((await rejection(c.liftNoGo(company.noGoId, 'Again', opt()))).code).toBe('not_found');
  });
});

describe('sending writes', () => {
  it('pauseSending and resumeSending toggle running', async () => {
    const c = client();
    const paused = await c.pauseSending(opt());
    expect(paused).toMatchObject({ state: 'paused_by_you', nextSend: null });
    expect((await c.shell()).sending.state).toBe('paused_by_you');
    expect((await c.outbox()).sending.state).toBe('paused_by_you');
    const resumed = await c.resumeSending(false, opt());
    expect(resumed).toMatchObject({ state: 'running', pausedAt: null });
    expect(resumed.nextSend?.personName).toBe('Jordan Price');
  });

  it('resumeSending after a hard block needs acknowledge', async () => {
    const c = client('hard_block');
    expect((await rejection(c.resumeSending(false, opt()))).code).toBe('not_allowed');
    expect((await c.outbox()).sending.state).toBe('paused_hard_block');
    const resumed = await c.resumeSending(true, opt());
    expect(resumed).toMatchObject({ state: 'running', gmailMessage: null });
    expect((await c.shell()).sending.state).toBe('running');
  });

  it('a signed out sender cannot be resumed from the app', async () => {
    const c = client('signed_out');
    expect((await rejection(c.resumeSending(true, opt()))).code).toBe('not_allowed');
  });
});

describe('LinkedIn, Needs you', () => {
  it('logLinkedIn moves Hannah Cole from to_reach to waiting', async () => {
    const c = client();
    const find = async () => (await c.linkedin()).groups.flatMap((g) => g.people).find((p) => p.person.name === 'Hannah Cole');
    const hannah = await find();
    if (!hannah) throw new Error('no Hannah');
    expect(hannah.tab).toBe('to_reach');
    const logged = await c.logLinkedIn(hannah.person.id, 'request_sent', opt());
    expect(logged).toMatchObject({ tab: 'waiting', steps: [{ step: 'request_sent', at: '2026-11-10T15:14:00Z' }] });
    const view = await c.linkedin();
    expect(view.counts).toMatchObject({ toReach: 11, waiting: 10, replied: 2 });
    expect((await c.shell()).linkedinToReach).toBe(11);
    expect((await find())?.tab).toBe('waiting');
    expect((await c.logLinkedIn(hannah.person.id, 'request_sent', opt())).steps).toHaveLength(1);
    expect((await rejection(c.logLinkedIn(1, 'request_sent', opt()))).code).toBe('not_found');
  });

  it('logLinkedIn replied records the reply kind and a no-go for said_no', async () => {
    const c = client();
    const luis = (await c.linkedin()).groups.flatMap((g) => g.people).find((p) => p.person.name === 'Luis Romero');
    if (!luis) throw new Error('no Luis');
    const logged = await c.logLinkedIn(luis.person.id, 'replied', opt(), 'said_no');
    expect(logged).toMatchObject({ tab: 'replied', replyKind: 'said_no' });
    expect((await c.linkedin()).counts).toMatchObject({ toReach: 12, waiting: 8, replied: 3 });
    expect((await c.noGo()).people.some((r) => r.target.kind === 'person' && r.target.person.name === 'Luis Romero' && r.reason === 'said_no')).toBe(true);
  });

  it('resolveReview edit needs a source', async () => {
    const c = client();
    const item = (await c.needsYou()).open.find((r) => r.kind === 'domain_missing');
    if (!item) throw new Error('no domain item');
    const edit = (source: string) => c.resolveReview(item.id, { kind: 'edit', field: 'domain', value: 'cerebrium.example', source }, item.revision, opt());
    expect((await rejection(edit(''))).code).toBe('not_allowed');
    expect((await rejection(edit('   '))).code).toBe('not_allowed');
    expect((await c.needsYou()).open).toHaveLength(5);

    await edit('Their careers page footer');
    const after = await c.needsYou();
    expect(after.open).toHaveLength(4);
    expect(after.resolvedThisWeek[0]).toMatchObject({ kind: 'domain_missing', companyName: 'Cerebrium', resolution: 'field_edited', locked: true });
    expect((await c.shell()).openReviewItems).toBe(4);
    expect((await c.company(item.company.id)).company.domain).toBe('cerebrium.example');
    expect((await c.searchIndex()).companies.find((x) => x.name === 'Cerebrium')?.domain).toBe('cerebrium.example');
  });

  it('resolveReview ok marks it resolved, a stale revision conflicts, and no_go closes the company', async () => {
    const c = client();
    const open = (await c.needsYou()).open;
    const hex = open.find((r) => r.company.name === 'Hex');
    const zed = open.find((r) => r.company.name === 'Zed Industries');
    if (!hex || !zed) throw new Error('missing items');
    expect((await rejection(c.resolveReview(hex.id, { kind: 'ok' }, 0, opt()))).code).toBe('conflict');
    await c.resolveReview(hex.id, { kind: 'ok' }, hex.revision, opt());
    expect((await c.needsYou()).resolvedThisWeek[0]).toMatchObject({ companyName: 'Hex', resolution: 'marked_ok', locked: false });

    await c.resolveReview(zed.id, { kind: 'no_go', reason: 'other', source: 'Board is gone' }, zed.revision, opt());
    const noGo = await c.noGo();
    expect(noGo.companies.some((r) => r.kind === 'no_go' && r.target.kind === 'company' && r.target.company.name === 'Zed Industries' && r.reason === 'other')).toBe(true);
    expect((await c.needsYou()).resolvedThisWeek[0]).toMatchObject({ companyName: 'Zed Industries', resolution: 'marked_no_go' });
    expect((await c.company(zed.company.id)).company.status).toBe('closed');
  });
});

describe('Needs you: merge, Undo from Resolved and the settled count (#16)', () => {
  const openItem = async (c: FixtureClient, name: string) => {
    const found = (await c.needsYou()).open.find((r) => r.company.name === name);
    if (!found) throw new Error(`No open item for ${name}`);
    return found;
  };
  const resolvedRow = async (c: FixtureClient, name: string) => {
    const found = (await c.needsYou()).resolvedThisWeek.find((r) => r.companyName === name);
    if (!found) throw new Error(`No resolved row for ${name}`);
    return found;
  };

  it('reopenReview puts an ok item back open and the shell count rises', async () => {
    const c = client();
    const hex = await openItem(c, 'Hex');
    await c.resolveReview(hex.id, { kind: 'ok' }, hex.revision, opt());
    expect((await c.shell()).openReviewItems).toBe(4);
    const row = await resolvedRow(c, 'Hex');
    expect(row).toMatchObject({ resolution: 'marked_ok', canUndo: true, locked: false });

    await c.reopenReview(row.id, row.revision, opt());
    const back = await openItem(c, 'Hex');
    expect(back.revision).toBe(row.revision + 1);
    expect(back.quote?.text).toBe(hex.quote?.text);
    expect((await c.needsYou()).resolvedThisWeek.some((r) => r.id === row.id)).toBe(false);
    expect((await c.shell()).openReviewItems).toBe(5);
    // Resolving it again works from the new revision.
    await c.resolveReview(back.id, { kind: 'ok' }, back.revision, opt());
    expect((await c.shell()).openReviewItems).toBe(4);
  });

  it('reopenReview on a no-go takes the company off the no-go list again', async () => {
    const c = client();
    const zed = await openItem(c, 'Zed Industries');
    await c.resolveReview(zed.id, { kind: 'no_go', reason: 'other', source: 'Board is gone' }, zed.revision, opt());
    const row = await resolvedRow(c, 'Zed Industries');
    expect(row.canUndo).toBe(true);
    await c.reopenReview(row.id, row.revision, opt());
    const noGo = await c.noGo();
    expect(noGo.companies.some((r) => r.kind === 'no_go' && r.target.kind === 'company' && r.target.company.name === 'Zed Industries')).toBe(false);
    expect((await c.company(zed.company.id)).company.status).toBe('candidate');
    expect(await openItem(c, 'Zed Industries')).toMatchObject({ kind: 'board_unreadable' });
  });

  it('reopenReview on a locked edit throws not_allowed with the lock message', async () => {
    const c = client();
    const turso = await resolvedRow(c, 'Turso');
    expect(turso).toMatchObject({ resolution: 'field_edited', locked: true, canUndo: false });
    const err = await rejection(c.reopenReview(turso.id, turso.revision, opt()));
    expect(err.code).toBe('not_allowed');
    expect(err.message).toBe('A value you set is locked and cannot be taken back here.');
    expect((await c.shell()).openReviewItems).toBe(5);
  });

  it('reopenReview with a stale revision throws conflict', async () => {
    const c = client();
    const railway = await resolvedRow(c, 'Railway');
    const err = await rejection(c.reopenReview(railway.id, railway.revision - 1, opt()));
    expect(err.code).toBe('conflict');
    expect(err.message).toBe('This item changed since you loaded it. Nothing was saved.');
    expect((await c.needsYou()).open.some((r) => r.company.name === 'Railway')).toBe(false);
    // The right revision brings a row the fixture started with back open.
    await c.reopenReview(railway.id, railway.revision, opt());
    expect(await openItem(c, 'Railway')).toMatchObject({ kind: 'sponsorship_unclear' });
    expect((await rejection(c.reopenReview(railway.id, railway.revision + 1, opt()))).code).toBe('not_allowed');
    expect((await rejection(c.reopenReview(1, 1, opt()))).code).toBe('not_found');
  });

  it('resolveReview merge removes the loser and the item', async () => {
    const c = client('review');
    const item = await openItem(c, 'Mesh Systems');
    if (!item.merge) throw new Error('no merge compare');
    const loser = item.merge.b.companyId;
    expect((await c.company(loser)).company.status).toBe('backlog');
    expect((await rejection(c.resolveReview((await openItem(c, 'Hex')).id, { kind: 'merge', keep: 'a' }, 1, opt()))).code).toBe('not_allowed');

    await c.resolveReview(item.id, { kind: 'merge', keep: item.merge.suggestedKeep }, item.revision, opt());
    expect((await c.needsYou()).open.some((r) => r.id === item.id)).toBe(false);
    expect((await c.company(loser)).company.status).toBe('removed');
    expect((await c.shell()).openReviewItems).toBe(10);
    const row = await resolvedRow(c, 'Mesh Systems');
    expect(row).toMatchObject({ kind: 'merge_candidate', note: 'Merged Mesh into Mesh Systems by you', locked: false, canUndo: true });

    // Undo from Resolved unmerges: the loser comes back as it was, and the question is open again.
    await c.reopenReview(row.id, row.revision, opt());
    expect((await c.company(loser)).company.status).toBe('backlog');
    expect((await openItem(c, 'Mesh Systems')).merge?.b.name).toBe('Mesh');
    expect((await c.shell()).openReviewItems).toBe(11);
  });

  it('keep separate on a merge question is the ok fix and says so', async () => {
    const c = client('review');
    const item = await openItem(c, 'Mesh Systems');
    await c.resolveReview(item.id, { kind: 'ok' }, item.revision, opt());
    expect(await resolvedRow(c, 'Mesh Systems')).toMatchObject({ resolution: 'marked_ok', note: 'Kept separate by you', canUndo: true });
  });

  it('settledToday rises by one after a resolve', async () => {
    const c = client();
    expect((await c.needsYou()).settledToday).toBe(0);
    const hex = await openItem(c, 'Hex');
    await c.resolveReview(hex.id, { kind: 'ok' }, hex.revision, opt());
    expect((await c.needsYou()).settledToday).toBe(1);
    const trigger = await openItem(c, 'Trigger.dev');
    await c.resolveReview(trigger.id, { kind: 'edit', field: 'size', value: 'about 30', source: 'Team page' }, trigger.revision, opt());
    const after = await c.needsYou();
    expect(after.settledToday).toBe(2);
    expect(after.resolvedThisWeek[0]).toMatchObject({ note: 'Size set to about 30 by you, from Team page', locked: true, canUndo: false });
    // Undo takes it back off the count.
    const row = await resolvedRow(c, 'Hex');
    await c.reopenReview(row.id, row.revision, opt());
    expect((await c.needsYou()).settledToday).toBe(1);
  });
});

describe('write plumbing', () => {
  it('every write rejects with network in the error scenario', async () => {
    const c = client('error');
    expect((await rejection(c.pauseSending(opt()))).code).toBe('network');
    expect((await rejection(c.pick(500, 1, opt()))).code).toBe('network');
    expect((await rejection(c.liftNoGo(1, 'x', opt()))).code).toBe('network');
  });

  it('unknown ids are not_found', async () => {
    const c = client();
    expect((await rejection(c.approve(1, 1, opt()))).code).toBe('not_found');
    expect((await rejection(c.pick(1, 1, opt()))).code).toBe('not_found');
    expect((await rejection(c.pauseCompany(1, 1, opt()))).code).toBe('not_found');
    expect((await rejection(c.resolveReview(1, { kind: 'ok' }, 1, opt()))).code).toBe('not_found');
    expect((await rejection(c.setRoleKind(1, 'engineer', 1, opt()))).code).toBe('not_found');
  });

  it('returned values are copies', async () => {
    const c = client();
    const omar = await itemFor(c, 'Omar Haddad');
    const detail = await c.approve(omar.messageId, omar.revision, opt());
    detail.body = 'changed';
    detail.item.state = 'sent';
    const again = await detailOf(c, omar);
    expect(again.body).not.toBe('changed');
    expect(again.item.state).toBe('approved');
  });

  it('a repeated requestId returns a copy of the first result', async () => {
    const c = client();
    const omar = await itemFor(c, 'Omar Haddad');
    const o = opt();
    const first = await c.approve(omar.messageId, omar.revision, o);
    first.item.state = 'sent';
    const second = await c.approve(omar.messageId, omar.revision, o);
    expect(second.item.state).toBe('approved');
  });

  it('a failed write does nothing and its requestId can be used again', async () => {
    const c = client();
    const kenji = await itemFor(c, 'Kenji Watanabe');
    const o = opt();
    expect((await rejection(c.approve(kenji.messageId, kenji.revision, o))).code).toBe('blocked');
    expect((await rejection(c.approve(kenji.messageId, kenji.revision, o))).code).toBe('blocked');
    expect((await c.outbox()).counts.review).toBe(7);
  });

  it('writes wait for the configured latency', async () => {
    const c = new FixtureClient(buildFixture(), { latencyMs: 20 });
    const started = Date.now();
    await c.pauseSending(opt());
    expect(Date.now() - started).toBeGreaterThanOrEqual(15);
  });
});

describe('derived fields stay consistent', () => {
  it('every outbox group matches the item state at the start', async () => {
    const c = client();
    const items = (await c.outbox()).items;
    const states = new Set(items.map((i) => `${i.group}:${i.state}:${i.touchState}`));
    expect(states.size).toBeGreaterThan(8);
    // A write that touches one row re-derives its group; an approve-unapprove round trip must land every row where it began.
    const before = new Map(items.map((i) => [i.touchId, i.group]));
    const omar = items.find((i) => i.person.name === 'Omar Haddad');
    if (!omar) throw new Error('no Omar');
    await c.approve(omar.messageId, omar.revision, opt());
    await c.unapprove(omar.messageId, omar.revision + 1, opt());
    const after = (await c.outbox()).items;
    after.forEach((i) => expect(i.group).toBe(before.get(i.touchId)));
  });

  it('the Sent group is newest first by sortKey', async () => {
    const c = client();
    const sent = (await c.outbox()).items.filter((i) => i.group === 'sent').sort((a, b) => (a.sortKey < b.sortKey ? -1 : 1));
    expect(sent).toHaveLength(64);
    for (let i = 1; i < sent.length; i++) expect((sent[i - 1]?.at ?? '') >= (sent[i]?.at ?? '')).toBe(true);
  });

  it('detail items match the outbox rows after writes', async () => {
    const c = client();
    const omar = await itemFor(c, 'Omar Haddad');
    await c.approve(omar.messageId, omar.revision, opt());
    const sara = await itemFor(c, 'Sara Lindqvist');
    await c.skip(sara.messageId, sara.revision, opt());
    for (const touchId of [omar.touchId, sara.touchId] as Id[]) {
      const row = (await c.outbox()).items.find((i) => i.touchId === touchId);
      expect((await c.outboxDetail(touchId)).item).toEqual(row);
    }
  });
});

/* ------------------------------------------------------------------ the people plan (#33) */

const rowAt = async (c: FixtureClient, n: number): Promise<PlanRow> => {
  const row = (await c.plan()).rows[n - 1];
  if (!row) throw new Error(`No plan row ${n}`);
  return row;
};
const personOf = (row: PlanRow): Id => {
  if (!row.person) throw new Error('Row has no person');
  return row.person.person.id;
};
const postingOf = (row: PlanRow): Id => {
  if (!row.role) throw new Error('Row has no role');
  return row.role.postingId;
};
const requestKey = (row: PlanRow) => `plan:${row.entryId}:request`;
const applyKey = (row: PlanRow) => `plan:${row.entryId}:apply`;
const logReq = (c: FixtureClient, row: PlanRow, key = requestKey(row)) => c.logRequest(row.entryId, { personId: personOf(row), idempotencyKey: key }, row.revision, opt());
const logApply = (c: FixtureClient, row: PlanRow, key = applyKey(row)) => c.logApplied(row.entryId, { postingId: postingOf(row), idempotencyKey: key }, row.revision, opt());

describe('plan writes: logging', () => {
  it('logRequest marks the request step at asOf and moves progress', async () => {
    const c = client();
    const row = await rowAt(c, 3);
    const out = await logReq(c, row);
    const plan = await c.plan();
    const step = plan.rows[2]?.steps[0];
    expect(step?.at).toBe(plan.asOf);
    expect(step?.touchId).not.toBeNull();
    expect(out.steps[0]).toEqual(step);
    const progress = await c.progress();
    expect(progress.today.requests).toBe(3);
    expect(progress.today.applications).toBe(2);
    expect((await c.shell()).plan.done).toBe(2);
    expect(plan.rows[2]?.state).toBe('todo');
    expect(plan.week.requests).toBe(32);
    expect(progress.week.stages[0]?.count).toBe(32);
    expect(progress.week.stages[1]?.from).toEqual({ stage: 'requests', count: 32 });
    expect(progress.month.stages[0]?.count).toBe(53);
    expect(progress.week.effort[0]?.count).toBe(32);
    expect(progress.month.effort[0]?.count).toBe(53);
    expect(progress.base.requests).toBe(143);
    expect(plan.linkedinWeek).toMatchObject({ used: 27, hardUsed: 32 });
  });

  it('the apply step completes the row', async () => {
    const c = client();
    const row = await rowAt(c, 3);
    await logReq(c, row);
    const out = await logApply(c, await rowAt(c, 3));
    expect(out.state).toBe('done');
    expect((await rowAt(c, 3)).state).toBe('done');
    const plan = await c.plan();
    expect(plan.done).toBe(3);
    expect(plan.total).toBe(7);
    expect((await c.shell()).plan).toEqual({ done: 3, total: 7 });
    const progress = await c.progress();
    expect(progress.today).toMatchObject({ planned: 7, done: 3, requests: 3, applications: 3 });
    expect(progress.week.effort[1]?.count).toBe(25);
    expect(plan.week.requests).toBe(32);
  });

  it('applying first leaves the row open until the request is logged too', async () => {
    const c = client();
    const out = await logApply(c, await rowAt(c, 3));
    expect(out.state).toBe('todo');
    expect(out.steps[1]?.at).not.toBeNull();
    expect((await c.plan()).done).toBe(2);
  });

  it('same key twice makes one touch', async () => {
    const c = client();
    const row = await rowAt(c, 3);
    const first = await logReq(c, row, 'plan:6003:request');
    const second = await c.logRequest(row.entryId, { personId: personOf(row), idempotencyKey: 'plan:6003:request' }, row.revision, opt());
    expect(second.steps[0]?.touchId).toBe(first.steps[0]?.touchId);
    expect(second).toEqual(await rowAt(c, 3));
    expect((await c.progress()).today.requests).toBe(3);
    expect((await c.plan()).week.requests).toBe(32);
    expect((await c.plan()).linkedinWeek.used).toBe(27);
  });

  it('same key twice on the apply step makes one touch', async () => {
    const c = client();
    const row = await rowAt(c, 3);
    const first = await logApply(c, row, 'plan:6003:apply');
    const second = await logApply(c, row, 'plan:6003:apply');
    expect(second.steps[1]?.touchId).toBe(first.steps[1]?.touchId);
    expect((await c.progress()).today.applications).toBe(3);
    expect((await c.progress()).week.effort[1]?.count).toBe(25);
  });

  it('same requestId twice does the work once', async () => {
    const c = client();
    const row = await rowAt(c, 3);
    const o = opt();
    const input = { personId: personOf(row), idempotencyKey: requestKey(row) };
    const first = await c.logRequest(row.entryId, input, row.revision, o);
    const second = await c.logRequest(row.entryId, input, row.revision, o);
    expect(second).toEqual(first);
    expect((await c.progress()).today.requests).toBe(3);
    first.steps[0] = { kind: 'request', touchId: null, at: null };
    expect((await rowAt(c, 3)).steps[0]?.at).not.toBeNull();
  });

  it('logging a step that is already logged under another key is refused and changes nothing, as the API does', async () => {
    const c = client();
    const row = await rowAt(c, 1);
    const err = await rejection(c.logRequest(row.entryId, { personId: personOf(row), idempotencyKey: 'another-key' }, row.revision, opt()));
    expect(err.code).toBe('not_allowed');
    expect(err.message).toMatch(/^Already logged/);
    expect(await rowAt(c, 1)).toEqual(row);
    expect((await c.progress()).today.requests).toBe(2);
  });

  it('logApplied with no role is refused', async () => {
    const c = client();
    const mintlify = await rowAt(c, 7);
    expect(mintlify.role).toBeNull();
    const err = await rejection(c.logApplied(mintlify.entryId, { postingId: 1, idempotencyKey: applyKey(mintlify) }, mintlify.revision, opt()));
    expect(err.code).toBe('not_allowed');
  });

  it('a step log checks the row revision, as the API does: a stale one conflicts and logs nothing', async () => {
    const c = client();
    const row = await rowAt(c, 3);
    const first = await logReq(c, row);
    expect(first.revision).toBe(row.revision + 1);
    // The second step, sent with the revision the row had before the first step moved it on.
    const stale = await rejection(c.logApplied(row.entryId, { postingId: postingOf(row), idempotencyKey: applyKey(row) }, row.revision, opt()));
    expect(stale.code).toBe('conflict');
    expect((await c.progress()).today.applications).toBe(2);
    // With the revision the first log answered, it lands.
    const out = await c.logApplied(row.entryId, { postingId: postingOf(row), idempotencyKey: applyKey(row) }, first.revision, opt());
    expect(out.state).toBe('done');
    expect((await c.plan()).done).toBe(3);
    // The same key again is a replay: the API finds the touch before it looks at the revision, so any revision does.
    const again = await c.logApplied(row.entryId, { postingId: postingOf(row), idempotencyKey: applyKey(row) }, 0, opt());
    expect(again.steps[1]?.touchId).toBe(out.steps[1]?.touchId);
    expect((await c.progress()).today.applications).toBe(3);
    expect((await c.progress()).today.requests).toBe(3);
  });

  it('a stale revision still rejects skip, after a step log moved the row on', async () => {
    const c = client();
    const row = await rowAt(c, 3);
    await logReq(c, row);
    expect((await rejection(c.skipRow(row.entryId, row.revision, opt()))).code).toBe('conflict');
    expect((await c.plan()).rows[2]?.state).toBe('todo');
  });

  it('a row with no person cannot log a request, and an unknown row is not_found', async () => {
    const c = client();
    const cerebrium = await rowAt(c, 5);
    expect(cerebrium.person).toBeNull();
    const err = await rejection(c.logRequest(cerebrium.entryId, { personId: 1, idempotencyKey: requestKey(cerebrium) }, cerebrium.revision, opt()));
    expect(err.code).toBe('not_allowed');
    const email = await rowAt(c, 7);
    expect((await rejection(c.logRequest(email.entryId, { personId: 1, idempotencyKey: requestKey(email) }, email.revision, opt()))).code).toBe('not_allowed');
    expect((await rejection(c.logRequest(1, { personId: 1, idempotencyKey: 'plan:1:request' }, 1, opt()))).code).toBe('not_found');
    const row = await rowAt(c, 3);
    expect((await rejection(c.logRequest(row.entryId, { personId: personOf(row) + 1, idempotencyKey: requestKey(row) }, row.revision, opt()))).code).toBe('not_allowed');
  });

  it('a write in one fixture does not leak into the next one', async () => {
    const a = client();
    const row = await rowAt(a, 3);
    await logReq(a, row);
    await a.saveNote(row.note?.noteId ?? 0, { text: 'Changed', reasonLine: 'Changed line', revision: row.note?.revision ?? 0 }, opt());
    await a.skipRow((await rowAt(a, 4)).entryId, (await rowAt(a, 4)).revision, opt());
    const b = client();
    const fresh = await rowAt(b, 3);
    expect(fresh.steps[0]?.at).toBeNull();
    expect(fresh.note?.text).not.toBe('Changed');
    expect(fresh.note?.reasonState).toBe('confirmed');
    expect((await rowAt(b, 4)).state).toBe('todo');
    expect((await b.plan()).tomorrow.total).toBe(25);
  });

  it('no two fixtures share a source link or a note fact', () => {
    const [a, b] = [buildFixture(), buildFixture()];
    a.plan.rows.forEach((row, i) => {
      const other = b.plan.rows[i];
      row.reasonSources.forEach((src, k) => expect(src).not.toBe(other?.reasonSources[k]));
      row.note?.facts.forEach((f, k) => {
        expect(f).not.toBe(other?.note?.facts[k]);
        expect(f.source).not.toBe(other?.note?.facts[k]?.source);
      });
    });
  });

  it('writes reject with network in the error scenario', async () => {
    const c = client('error');
    expect((await rejection(c.logRequest(6003, { personId: 1, idempotencyKey: 'k' }, 1, opt()))).code).toBe('network');
    expect((await rejection(c.logOutcome(2201, 'quiet', 1, opt()))).code).toBe('network');
    expect((await rejection(c.dismissWatch(7101, opt()))).code).toBe('network');
  });
});

describe('plan writes: outcomes', () => {
  const firstPrompt = async (c: FixtureClient) => {
    const t = (await c.outcomePrompt()).touches[0];
    if (!t) throw new Error('No prompt touch');
    return t;
  };

  it('logOutcome accepted makes an unseen win and clears the prompt', async () => {
    const c = client();
    const t = await firstPrompt(c);
    const before = await c.wins();
    const out = await c.logOutcome(t.touchId, 'accepted', t.revision, opt());
    expect(out.revision).toBe(t.revision + 1);
    const wins = await c.wins();
    expect(wins.unseen).toBe(before.unseen + 1);
    expect(wins.counts.accepted).toBe(before.counts.accepted + 1);
    const win = wins.wins.find((w) => w.touchId === t.touchId);
    expect(win).toMatchObject({ kind: 'accepted', unseen: true, at: wins.asOf, line: `${t.person?.name} at ${t.company.name} accepted your request.` });
    expect((await c.outcomePrompt()).touches).toHaveLength(1);
    expect((await c.shell()).winsUnseen).toBe(1);
    expect((await c.progress()).today.accepted).toBe(1);
    expect((await c.progress()).week.stages[1]?.count).toBe(10);
    expect((await c.plan()).week.accepts).toBe(10);
  });

  it('quiet clears the prompt without a win', async () => {
    const c = client();
    const t = await firstPrompt(c);
    await c.logOutcome(t.touchId, 'quiet', t.revision, opt());
    expect((await c.outcomePrompt()).touches).toHaveLength(1);
    const wins = await c.wins();
    expect(wins.unseen).toBe(0);
    expect(wins.wins).toHaveLength(5);
    expect((await c.progress()).week.stages[1]?.count).toBe(9);
  });

  it('a reply on a quiet touch is accepted', async () => {
    const c = client();
    const t = await firstPrompt(c);
    const { revision } = await c.logOutcome(t.touchId, 'quiet', t.revision, opt());
    await c.logOutcome(t.touchId, 'replied', revision, opt(), 'interested');
    const wins = await c.wins();
    expect(wins.wins.find((w) => w.touchId === t.touchId)).toMatchObject({ kind: 'replied', replyKind: 'interested', unseen: true });
    expect((await c.outcomePrompt()).touches).toHaveLength(1);
    expect((await c.plan()).week.replies).toBe(3);
  });

  it('a reply that is not now is recognised and stays out of the prompt', async () => {
    const c = client();
    const t = await firstPrompt(c);
    await c.logOutcome(t.touchId, 'replied', t.revision, opt(), 'not_now');
    const win = (await c.wins()).wins.find((w) => w.touchId === t.touchId);
    expect(win).toMatchObject({ kind: 'replied', replyKind: 'not_now', unseen: true });
    expect((await c.outcomePrompt()).touches).toHaveLength(1);
  });

  it('said no is not a win', async () => {
    const c = client();
    const t = await firstPrompt(c);
    await c.logOutcome(t.touchId, 'replied', t.revision, opt(), 'said_no');
    expect((await c.outcomePrompt()).touches).toHaveLength(1);
    const wins = await c.wins();
    expect(wins.unseen).toBe(0);
    expect(wins.wins.some((w) => w.touchId === t.touchId)).toBe(false);
    expect((await c.progress()).week.stages[2]?.count).toBe(3);
    expect((await c.plan()).week.replies).toBe(3);
    expect((await c.noGo()).people.some((r) => r.target.kind === 'person' && r.target.person.id === t.person?.id)).toBe(true);
  });

  it('asked to stop is a reply with no win', async () => {
    const c = client();
    const t = await firstPrompt(c);
    await c.logOutcome(t.touchId, 'replied', t.revision, opt(), 'asked_to_stop');
    expect((await c.wins()).unseen).toBe(0);
    expect((await c.plan()).week.replies).toBe(3);
  });

  it('a referral counts the reply it implies once', async () => {
    const c = client();
    const t = await firstPrompt(c);
    const first = await c.logOutcome(t.touchId, 'referred', t.revision, opt());
    const progress = await c.progress();
    expect(progress.week.stages[2]?.count).toBe(3);
    expect(progress.week.stages[3]).toMatchObject({ count: 1, from: { stage: 'replies', count: 3 } });
    expect((await c.wins()).wins.find((w) => w.touchId === t.touchId)?.kind).toBe('referred');
    await c.logOutcome(t.touchId, 'replied', first.revision, opt(), 'interested');
    expect((await c.progress()).week.stages[2]?.count).toBe(3);
  });

  it('an interview makes a win and an offer makes one with no funnel stage', async () => {
    const c = client();
    const [a, b] = (await c.outcomePrompt()).touches;
    if (!a || !b) throw new Error('Need two prompt touches');
    await c.logOutcome(a.touchId, 'interview', a.revision, opt());
    await c.logOutcome(b.touchId, 'offer', b.revision, opt());
    const wins = await c.wins();
    expect(wins.counts).toMatchObject({ interview: 2, offer: 1 });
    expect(wins.unseen).toBe(2);
    expect((await c.progress()).week.stages[4]?.count).toBe(2);
    expect((await c.outcomePrompt()).touches).toHaveLength(0);
  });

  it('the same outcome twice makes one win', async () => {
    const c = client();
    const t = await firstPrompt(c);
    const first = await c.logOutcome(t.touchId, 'accepted', t.revision, opt());
    await c.logOutcome(t.touchId, 'accepted', first.revision, opt());
    expect((await c.wins()).unseen).toBe(1);
    expect((await c.plan()).week.accepts).toBe(10);
  });

  it('a touch from a logged request can get an outcome', async () => {
    const c = client();
    const row = await rowAt(c, 3);
    const logged = await logReq(c, row);
    const touchId = logged.steps[0]?.touchId;
    if (touchId === null || touchId === undefined) throw new Error('No touch');
    await c.logOutcome(touchId, 'accepted', 1, opt());
    const win = (await c.wins()).wins.find((w) => w.touchId === touchId);
    expect(win).toMatchObject({ kind: 'accepted', company: { name: 'Pylon' }, role: 'Software Engineer I' });
    expect(win?.person?.name).toBe('Lena Ortiz');
    expect(win?.sentNote).toBe(row.note?.text);
  });

  it('stale revisions conflict and unknown touches are not_found', async () => {
    const c = client();
    const t = await firstPrompt(c);
    expect((await rejection(c.logOutcome(t.touchId, 'accepted', t.revision + 3, opt()))).code).toBe('conflict');
    expect((await rejection(c.logOutcome(1, 'accepted', 1, opt()))).code).toBe('not_found');
    expect((await c.wins()).unseen).toBe(0);
    expect((await c.outcomePrompt()).touches).toHaveLength(2);
  });

  it('same requestId twice logs the outcome once', async () => {
    const c = client();
    const t = await firstPrompt(c);
    const o = opt();
    const first = await c.logOutcome(t.touchId, 'accepted', t.revision, o);
    const second = await c.logOutcome(t.touchId, 'accepted', t.revision, o);
    expect(second).toEqual(first);
    expect((await c.wins()).unseen).toBe(1);
  });
});

describe('plan writes: skip, notes, wins, watch', () => {
  it('skipRow carries the row', async () => {
    const c = client();
    const before = await c.plan();
    const row = await rowAt(c, 4);
    const out = await c.skipRow(row.entryId, row.revision, opt());
    expect(out.state).toBe('skipped');
    const plan = await c.plan();
    expect(plan.rows[3]?.state).toBe('skipped');
    expect(plan.rows.map((r) => r.entryId)).toEqual(before.rows.map((r) => r.entryId));
    // As the API: the room stays, and tomorrow's draft proposes the carried person again, first among the people.
    expect(plan.tomorrow.total).toBe(before.tomorrow.total);
    expect(plan.tomorrow.preview[0]).toEqual({ companyName: 'Inngest', personName: 'Jonah Park', roleTitle: 'Software Engineer II', channel: 'linkedin_request', findSomeone: false });
    expect(plan.tomorrow.preview).toHaveLength(before.tomorrow.preview.length + 1);
    expect(plan.tomorrow.text).toBe(`Tomorrow's plan builds at 08:00. Room for 25 people. A draft of who it would propose is below.`);
    expect(plan.done).toBe(2);
    expect((await c.shell()).plan.done).toBe(2);
  });

  it('removeCompany takes its plan row off the day, as the API does, and restoreCompany brings it back carried', async () => {
    const c = client();
    const before = await c.plan();
    const pylon = await rowAt(c, 3);
    await c.removeCompany(pylon.company.id, 'not_a_fit', '', pylon.company.revision, opt());
    const plan = await c.plan();
    expect(plan.rows.map((r) => r.entryId)).toEqual(before.rows.map((r) => r.entryId).filter((id) => id !== pylon.entryId));
    expect(plan.total).toBe(before.total - 1);
    expect(plan.done).toBe(2);
    expect((await c.shell()).plan).toEqual({ done: 2, total: before.total - 1 });
    // A step on it is refused, with the API's words: the entry still exists, off the plan.
    const refused = await rejection(c.logRequest(pylon.entryId, { personId: pylon.person?.person.id ?? 0, idempotencyKey: 'plan:x:request' }, pylon.revision, opt()));
    expect(refused.code).toBe('not_allowed');
    expect(refused.message).toBe('That company was taken off the plan');

    const removed = (await c.noGo()).companies.flatMap((r) => (r.kind === 'removed' && r.company.id === pylon.company.id ? [r] : []))[0];
    if (!removed) throw new Error('not removed');
    await c.restoreCompany(pylon.company.id, 'Changed my mind', removed.company.revision, opt());
    // The API makes today's entry a backlog row, which the plan shows as carried to tomorrow.
    expect(await rowAt(c, 3)).toMatchObject({ entryId: pylon.entryId, state: 'skipped' });
    expect((await c.plan()).total).toBe(before.total);
  });

  it('a company with a step logged is not removed, as the API refuses one with a request or application out', async () => {
    const c = client();
    const dana = await rowAt(c, 1);
    const refused = await rejection(c.removeCompany(dana.company.id, 'other', '', dana.company.revision, opt()));
    expect(refused.code).toBe('not_allowed');
    expect(await rowAt(c, 1)).toMatchObject({ entryId: dana.entryId, state: 'done' });
  });

  it('a skipped find-someone row joins the draft first among the find-someone rows', async () => {
    const c = client();
    const row = await rowAt(c, 5);
    expect(row.person).toBeNull();
    await c.skipRow(row.entryId, row.revision, opt());
    const { preview } = (await c.plan()).tomorrow;
    const at = preview.findIndex((p) => p.companyName === row.company.name);
    expect(preview[at]).toMatchObject({ personName: null, findSomeone: true });
    expect(preview.slice(0, at).every((p) => !p.findSomeone)).toBe(true);
    expect(preview.slice(at).every((p) => p.findSomeone)).toBe(true);
  });

  it('skipRow on a done row is not_allowed, and a stale revision conflicts', async () => {
    const c = client();
    const done = await rowAt(c, 1);
    expect((await rejection(c.skipRow(done.entryId, done.revision, opt()))).code).toBe('not_allowed');
    const row = await rowAt(c, 4);
    expect((await rejection(c.skipRow(row.entryId, row.revision + 2, opt()))).code).toBe('conflict');
    expect((await rejection(c.skipRow(1, 1, opt()))).code).toBe('not_found');
    expect((await c.plan()).tomorrow.total).toBe(25);
  });

  it('a skipped row cannot be logged', async () => {
    const c = client();
    const row = await rowAt(c, 3);
    const skipped = await c.skipRow(row.entryId, row.revision, opt());
    const err = await rejection(c.logRequest(row.entryId, { personId: personOf(row), idempotencyKey: requestKey(row) }, skipped.revision, opt()));
    expect(err.code).toBe('not_allowed');
    expect((await c.progress()).today.requests).toBe(2);
  });

  const note3 = async (c: FixtureClient) => {
    const n = (await rowAt(c, 3)).note;
    if (!n) throw new Error('No note');
    return n;
  };
  const save = async (c: FixtureClient, text: string, reasonLine?: string) => {
    const n = await note3(c);
    return c.saveNote(n.noteId, { text, reasonLine: reasonLine ?? n.reasonLine, revision: n.revision }, opt());
  };

  it('saveNote rescans: a note over the limit', async () => {
    const c = client();
    const n = await note3(c);
    const text = 'x'.repeat(n.maxChars + 12);
    const out = await save(c, text);
    expect(out.chars).toBe(text.length);
    expect(out.checks.find((k) => k.code === 'note_too_long')).toMatchObject({ severity: 'block', title: 'Over the limit by 12 characters' });
    expect(out.checks[0]?.severity).toBe('block');
    expect((await note3(c)).checks.map((k) => k.code)).toContain('note_too_long');
    const fixed = await save(c, 'Hi Lena, a short note.');
    expect(fixed.checks.some((k) => k.severity === 'block')).toBe(false);
    expect(fixed.chars).toBe('Hi Lena, a short note.'.length);
  });

  it('saveNote rescans: a banned phrase and a dash', async () => {
    const c = client();
    const banned = await save(c, "Hi Lena, I'd love to hear how your team works.");
    expect(banned.checks.find((k) => k.code === 'banned_phrase')).toMatchObject({ severity: 'block' });
    expect(banned.checks.find((k) => k.code === 'banned_phrase')?.spans[0]).toMatchObject({ field: 'body', start: 9, end: 20 });
    const dashed = await save(c, `Hi Lena, your post ${EM_DASH} and the thread ${EN_DASH} was good.`);
    expect(dashed.checks.find((k) => k.code === 'dash')).toMatchObject({ severity: 'block' });
    expect(dashed.checks.find((k) => k.code === 'banned_phrase')).toBeUndefined();
    const clean = await save(c, 'Hi Lena, your post was good.');
    expect(clean.checks.every((k) => k.severity === 'pass')).toBe(true);
    expect(clean.checks).toHaveLength(3);
  });

  it('saveNote keeps the reason state unless the reason line changed', async () => {
    const c = client();
    const same = await save(c, 'Hi Lena, a short note.');
    expect(same).toMatchObject({ reasonState: 'confirmed', needsYourLine: false, savedBy: 'devanshu' });
    const changed = await save(c, 'Hi Lena, a short note.', 'Her team shipped usage billing.');
    expect(changed).toMatchObject({ reasonState: 'written', needsYourLine: false, reasonLine: 'Her team shipped usage billing.' });
    // A line Claude only proposed stays proposed through an edit of the text alone, but his note is his reason
    // (#9, 2026-10-26): nothing asks him to confirm Claude's line any more.
    const jonah = (await rowAt(c, 4)).note;
    if (!jonah) throw new Error('No note');
    expect(jonah.needsYourLine).toBe(true);
    const proposed = await c.saveNote(jonah.noteId, { text: 'Hi Jonah, a short note.', reasonLine: jonah.reasonLine, revision: jonah.revision }, opt());
    expect(proposed).toMatchObject({ reasonState: 'proposed', needsYourLine: false, savedBy: 'devanshu' });
    expect(proposed.revision).toBe(jonah.revision + 1);
  });

  it('saveNote marks a fact used only when his text holds its words', async () => {
    const c = client();
    const n = await note3(c);
    const [first] = n.facts;
    if (!first) throw new Error('No fact on the note');
    const kept = await save(c, `Hi Lena, ${first.text.toUpperCase()} was a good read.`);
    expect(kept.facts.map((f) => f.inNote)).toEqual(n.facts.map((f) => f === first));
    const dropped = await save(c, 'Hi Lena, your post was good.');
    expect(dropped.facts.every((f) => !f.inNote)).toBe(true);
    expect(dropped.facts.map((f) => f.text)).toEqual(n.facts.map((f) => f.text));
  });

  it('saveNote with a stale revision conflicts and an unknown note is not_found', async () => {
    const c = client();
    const n = await note3(c);
    expect((await rejection(c.saveNote(n.noteId, { text: 'x', reasonLine: n.reasonLine, revision: n.revision + 4 }, opt()))).code).toBe('conflict');
    expect((await rejection(c.saveNote(1, { text: 'x', reasonLine: 'y', revision: 1 }, opt()))).code).toBe('not_found');
    expect((await note3(c)).text).toBe(n.text);
  });

  it('confirmReason sets confirmed and clears needsYourLine', async () => {
    const c = client();
    const jonah = (await rowAt(c, 4)).note;
    if (!jonah) throw new Error('No note');
    expect(jonah.needsYourLine).toBe(true);
    const out = await c.confirmReason(jonah.noteId, jonah.revision, opt());
    expect(out).toMatchObject({ reasonState: 'confirmed', needsYourLine: false });
    expect((await rowAt(c, 4)).note).toMatchObject({ reasonState: 'confirmed', needsYourLine: false });
  });

  it('confirmReason with an empty reason line is not_allowed, and a stale revision conflicts', async () => {
    const c = client();
    const jonah = (await rowAt(c, 4)).note;
    if (!jonah) throw new Error('No note');
    const emptied = await c.saveNote(jonah.noteId, { text: jonah.text, reasonLine: '   ', revision: jonah.revision }, opt());
    expect((await rejection(c.confirmReason(jonah.noteId, emptied.revision, opt()))).code).toBe('not_allowed');
    expect((await rejection(c.confirmReason(jonah.noteId, jonah.revision, opt()))).code).toBe('conflict');
    expect((await rejection(c.confirmReason(1, 1, opt()))).code).toBe('not_found');
  });

  it('markWinsSeen zeroes unseen; an unknown id rejects not_found', async () => {
    const c = client('win');
    const wins = await c.wins();
    expect(wins.unseen).toBe(1);
    const unseen = wins.wins.find((w) => w.unseen);
    if (!unseen) throw new Error('No unseen win');
    expect((await rejection(c.markWinsSeen([unseen.winId, 1], opt()))).code).toBe('not_found');
    expect((await c.wins()).unseen).toBe(1);
    await c.markWinsSeen([unseen.winId], opt());
    const after = await c.wins();
    expect(after.unseen).toBe(0);
    expect(after.wins.every((w) => !w.unseen)).toBe(true);
    expect((await c.shell()).winsUnseen).toBe(0);
    expect((await c.progress()).today.accepted).toBe(1);
  });

  it('the day keeps its wins after they are seen, and a win from an earlier day is not today\'s', async () => {
    const c = client();
    const t = (await c.outcomePrompt()).touches[0];
    if (!t) throw new Error('No prompt touch');
    await c.logOutcome(t.touchId, 'replied', t.revision, opt(), 'interested');
    expect((await c.progress()).today).toMatchObject({ accepted: 0, replied: 1 });
    const all = (await c.wins()).wins;
    await c.markWinsSeen(all.map((w) => w.winId), opt());
    expect((await c.progress()).today).toMatchObject({ accepted: 0, replied: 1 });
  });

  it('dismissWatch removes the item', async () => {
    const c = client();
    const items = (await c.watchList()).items;
    expect(items).toHaveLength(3);
    const first = items[0];
    if (!first) throw new Error('No watch item');
    await c.dismissWatch(first.id, opt());
    expect((await c.watchList()).items.map((i) => i.id)).toEqual(items.slice(1).map((i) => i.id));
    expect((await rejection(c.dismissWatch(first.id, opt()))).code).toBe('not_found');
  });
});
