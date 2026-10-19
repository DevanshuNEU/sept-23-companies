import { ApiError, type WriteOptions } from '../client';
import type { Check, DailyEntry, Id, OutboxDetail, OutboxItem } from '../types';
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
