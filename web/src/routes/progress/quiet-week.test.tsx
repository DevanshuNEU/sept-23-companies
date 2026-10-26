import { screen, within } from '@testing-library/react';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture, type FixtureDb, type Scenario } from '@/api/fixture/data';
import { renderWithApp } from '@/test/render';
import ProgressPage from './index';

async function open(o: { scenario?: Scenario; route?: string; tweak?: (db: FixtureDb) => void } = {}) {
  const db = buildFixture(o.scenario ?? 'quiet_week');
  o.tweak?.(db);
  const view = renderWithApp(<ProgressPage />, { client: new FixtureClient(db), route: o.route ?? '/progress' });
  await screen.findByRole('heading', { level: 1, name: 'Progress' });
  await screen.findByRole('heading', { name: /^Your work this/ });
  return view;
}

const before = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
const BASE = 'About 1 in 16 requests gets a reply, and replies often take about 6 days.';

describe('QuietWeek', () => {
  it('quiet_week puts the effort list before the funnel', async () => {
    await open();
    const effort = screen.getByRole('list', { name: 'Your work this week' });
    const funnel = screen.getByRole('table', { name: /^Funnel/ });
    expect(before(effort, funnel)).toBe(true);
    expect(within(effort).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['28Requests sent', '22Applications sent', '6Emails reviewed']);
    expect(screen.getByRole('heading', { name: 'The week in counts' })).toBeInTheDocument();
  });

  it('puts the base rate between the work and the counts', async () => {
    await open();
    const effort = screen.getByRole('list', { name: 'Your work this week' });
    const rate = screen.getByText(BASE);
    const funnel = screen.getByRole('table', { name: /^Funnel/ });
    expect(before(effort, rate) && before(rate, funnel)).toBe(true);
  });

  it('shows the base rate and median days from his own data', async () => {
    await open();
    expect(screen.getByText(BASE)).toBeInTheDocument();
    expect(screen.getByText('From your own 142 requests since Sep 21: 41 accepted, 9 replies.')).toBeInTheDocument();
  });

  it('no median days drops the days clause', async () => {
    await open({ tweak: (db) => { db.progress.base.medianDaysToReply = null; } });
    expect(screen.getByText('About 1 in 16 requests gets a reply.')).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent('replies often take');
  });

  it('not enough outcomes shows the minimum sample line and no rate', async () => {
    await open({ tweak: (db) => { Object.assign(db.progress.base, { requests: 12, accepts: 3, replies: 0, enough: false, medianDaysToReply: null, since: '2026-11-02' }); } });
    expect(screen.getByText('Not enough outcomes yet to say what is usual. It takes about 30 requests.')).toBeInTheDocument();
    expect(screen.getByText('12 requests so far, since Nov 2.')).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/About 1 in|Most requests/);
  });

  it('zero replies ever shows no base rate line', async () => {
    await open({ tweak: (db) => { Object.assign(db.progress.base, { replies: 0, medianDaysToReply: null }); } });
    expect(document.body).not.toHaveTextContent(/About 1 in|Most requests|1 in 0|Not enough outcomes/);
    expect(screen.getByRole('list', { name: 'Your work this week' })).toBeInTheDocument();
  });

  it('the zero counts are grey, not red', async () => {
    const { container } = await open();
    expect(container.querySelector('[class*="text-bad"], [class*="bg-bad"], [class*="border-bad"]')).toBeNull();
    const funnel = screen.getByRole('table', { name: /^Funnel/ });
    const replied = within(funnel).getByRole('row', { name: /^Replied/ });
    expect(within(replied).getByText('0', { selector: 'td' })).toHaveClass('text-muted-foreground');
  });

  it('the non-quiet week does not use this arrangement', async () => {
    await open({ scenario: 'default' });
    expect(screen.queryByRole('heading', { name: 'The week in counts' })).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent('replies often take');
  });

  it('the month toggle keeps the same arrangement when the month is quiet', async () => {
    await open({ route: '/progress?period=month', tweak: (db) => { db.progress.month.quiet = true; } });
    expect(screen.getByRole('list', { name: 'Your work this month' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'The month in counts' })).toBeInTheDocument();
    expect(screen.getByText(BASE)).toBeInTheDocument();
  });

  it('a quiet week leaves the month in the usual order', async () => {
    await open({ route: '/progress?period=month' });
    expect(screen.queryByRole('heading', { name: 'The month in counts' })).not.toBeInTheDocument();
  });
});
