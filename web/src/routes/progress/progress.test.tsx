import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture, type Scenario } from '@/api/fixture/data';
import { renderWithApp } from '@/test/render';
import ProgressPage from './index';

const press = (key: string) => fireEvent.keyDown(document.body, { key });

function Where() {
  const { pathname, search } = useLocation();
  return <p data-testid="where">{pathname + search}</p>;
}

async function open(scenario: Scenario = 'default', route = '/progress') {
  const client = new FixtureClient(buildFixture(scenario));
  const view = renderWithApp(
    <>
      <ProgressPage />
      <Where />
    </>,
    { client, route },
  );
  await screen.findByRole('heading', { level: 1, name: 'Progress' });
  return view;
}

const where = () => screen.getByTestId('where').textContent;
const funnel = () => screen.getByRole('table', { name: /^Funnel/ });
const row = (stage: string) => {
  const r = within(funnel()).getByRole('row', { name: new RegExp(`^${stage}`) });
  return r;
};
const bar = (stage: string) => row(stage).querySelector<HTMLElement>('[data-slot="lbar"] > i');

describe('ProgressPage', () => {
  it('shows today against the plan', async () => {
    await open();
    const today = await screen.findByRole('table', { name: 'Today' });
    const cells = within(today).getAllByRole('cell').map((c) => c.textContent);
    expect(cells[0]).toBe('2 of 7 on the plan');
    // Requests, applications, accepted, replied: plain counts.
    expect(cells.slice(1)).toEqual(['2', '2', '0', '0']);
  });

  it('the week ledger shows 9 of 31 (29%) for accepts and 2 of 31 (6%) for replies', async () => {
    await open();
    await screen.findByRole('table', { name: /^Funnel/ });
    expect(row('Accepted')).toHaveTextContent('9 of 31 (29%) of requests');
    expect(row('Replied')).toHaveTextContent('2 of 31 (6%) of requests');
    expect(row('Requests sent')).toHaveTextContent('Where every rate starts');
  });

  it('referrals show 0 of 2 replies, with no percent: a rate of replies needs 20', async () => {
    await open();
    await screen.findByRole('table', { name: /^Funnel/ });
    expect(row('Referrals')).toHaveTextContent('0 of 2 of replies');
    expect(row('Referrals')).not.toHaveTextContent('%');
    expect(row('Interviews')).toHaveTextContent('1 of 2 of replies');
    expect(row('Interviews')).not.toHaveTextContent('%');
  });

  it("bars scale to the period's largest count, and a small non-zero count is never under 2 px", async () => {
    await open();
    await screen.findByRole('table', { name: /^Funnel/ });
    // 31 is the largest, so it fills the track. 9 of 31 is 29 percent of it.
    expect(bar('Requests sent')?.style.width).toBe('100%');
    expect(bar('Accepted')?.style.width).toBe(`${(9 / 31) * 100}%`);
    for (const stage of ['Requests sent', 'Accepted', 'Replied', 'Interviews']) {
      expect(bar(stage)?.style.minWidth).toBe('2px');
    }
    // Zero draws no bar and a plain grey 0.
    expect(bar('Referrals')).toBeNull();
    const zero = within(row('Referrals')).getByText('0', { selector: 'td' });
    expect(zero).toHaveClass('text-muted-foreground');
    // Every bar has a text alternative, so the table has an equivalent without it.
    expect(within(row('Accepted')).getByRole('img', { name: 'Accepted: 9 of 31 requests' })).toBeInTheDocument();
    expect(within(row('Referrals')).getByRole('img', { name: 'Referrals: 0' })).toBeInTheDocument();
  });

  it('bars are drawn against 30 when the period is small, so a first week looks small', async () => {
    await open('first_week');
    await screen.findByRole('table', { name: /^Funnel/ });
    expect(bar('Requests sent')?.style.width).toBe(`${(9 / 30) * 100}%`);
    expect(screen.getByText(/drawn against 30 requests/)).toBeInTheDocument();
  });

  it('first_week shows counts with no percent and the small-sample note', async () => {
    await open('first_week');
    await screen.findByRole('table', { name: /^Funnel/ });
    expect(row('Accepted')).toHaveTextContent('2 of 9 of requests');
    expect(row('Replied')).toHaveTextContent('0 of 9 of requests');
    expect(funnel()).not.toHaveTextContent('%');
    expect(screen.getByText('Only 9 requests so far. Rates this small swing a lot, so none are shown.')).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/NaN|Infinity/);
  });

  it('a stage with nothing to count against says None yet, never 0 of 0', async () => {
    await open('first_week');
    await screen.findByRole('table', { name: /^Funnel/ });
    // No replies in the first week, so referrals and interviews have no denominator.
    expect(row('Referrals')).toHaveTextContent('None yet');
    expect(row('Interviews')).toHaveTextContent('None yet');
    expect(funnel()).not.toHaveTextContent('of 0');
  });

  it('1 and 2 switch the period and the URL', async () => {
    const user = userEvent.setup();
    await open();
    await screen.findByRole('table', { name: /^Funnel/ });
    expect(screen.getByRole('tab', { name: /^Week/ })).toHaveAttribute('aria-selected', 'true');
    press('2');
    expect(await screen.findByRole('tab', { name: /^Month/ })).toHaveAttribute('aria-selected', 'true');
    expect(where()).toBe('/progress?period=month');
    press('1');
    expect(screen.getByRole('tab', { name: /^Week/ })).toHaveAttribute('aria-selected', 'true');
    expect(where()).toBe('/progress');
    await user.click(screen.getByRole('tab', { name: /^Month/ }));
    expect(where()).toBe('/progress?period=month');
  });

  it('month shows its own label and numbers', async () => {
    await open('default', '/progress?period=month');
    await screen.findByRole('table', { name: /^Funnel/ });
    expect(screen.getByRole('heading', { level: 2, name: /^This month, Nov 1 to 10/ })).toBeInTheDocument();
    expect(row('Requests sent')).toHaveTextContent('52');
    expect(row('Accepted')).toHaveTextContent('17 of 52 (33%) of requests');
    expect(row('Replied')).toHaveTextContent('4 of 52 (8%) of requests');
    expect(screen.getByRole('heading', { level: 2, name: 'Your work this month' })).toBeInTheDocument();
  });

  it('the week heading names its dates', async () => {
    await open();
    await screen.findByRole('table', { name: /^Funnel/ });
    expect(screen.getByRole('heading', { level: 2, name: /^Last 7 days, Nov 4 to 10/ })).toBeInTheDocument();
  });

  it('fresh shows "Nothing logged yet. Log a request on Today and it counts here." and no NaN', async () => {
    await open('fresh');
    expect(await screen.findByText('Nothing logged yet.')).toBeInTheDocument();
    expect(screen.getByText('Log a request on Today and it counts here.')).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/NaN|Infinity|of 0/);
    expect(screen.queryByRole('table', { name: /^Funnel/ })).not.toBeInTheDocument();
  });

  it('an empty plan says no plan today, never "of 0"', async () => {
    await open('empty');
    const today = await screen.findByRole('table', { name: 'Today' });
    expect(within(today).getByText('No plan today')).toBeInTheDocument();
    expect(today).not.toHaveTextContent('of 0');
  });

  it('no stat tile and no streak copy', async () => {
    await open();
    await screen.findByRole('table', { name: /^Funnel/ });
    expect(document.querySelector('[data-slot="stat-tile"], [class*="stat-tile"]')).toBeNull();
    expect(document.body).not.toHaveTextContent(/streak|overdue|behind|keep it up/i);
  });

  it('the effort list shows work done', async () => {
    await open();
    await screen.findByRole('table', { name: /^Funnel/ });
    const effort = screen.getByRole('list', { name: 'Your work this week' });
    expect(within(effort).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['31Requests sent', '24Applications sent', '8Emails reviewed']);
  });

  it('puts what the data says below the ledger', async () => {
    await open();
    const ledger = await screen.findByRole('table', { name: /^Funnel/ });
    const findings = screen.getByRole('heading', { level: 2, name: 'What the data says' });
    expect(Boolean(ledger.compareDocumentPosition(findings) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
    expect(await screen.findByText('Engineers on the team accepted more often than founders.')).toBeInTheDocument();
  });

  it('links to the win log', async () => {
    await open();
    await screen.findByRole('table', { name: /^Funnel/ });
    expect(screen.getByRole('link', { name: 'Open the win log' })).toHaveAttribute('href', '/wins');
  });
});
