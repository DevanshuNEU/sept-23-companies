import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router';
import type { DoneView } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture } from '@/api/fixture/data';
import { renderWithApp } from '@/test/render';
import DonePage from './index';

function Where() {
  const location = useLocation();
  return <p data-testid="where">{location.pathname + location.search}</p>;
}

async function done(client = new FixtureClient()) {
  const view = renderWithApp(
    <>
      <DonePage />
      <Where />
    </>,
    { route: '/done', client },
  );
  await screen.findByRole('heading', { level: 1, name: 'Done companies' });
  return view;
}

/** A fixture client whose Done metrics are changed by the test. */
function withMetrics(change: (m: DoneView['metrics']) => DoneView['metrics']) {
  class Changed extends FixtureClient {
    override async done(): Promise<DoneView> {
      const view = await super.done();
      return { ...view, metrics: change(view.metrics) };
    }
  }
  return new Changed();
}

const metric = (name: string) => screen.findByRole('group', { name });
const chart = () => screen.findByRole('img', { name: /^Replies per week/ });
const bars = (el: HTMLElement) => Array.from(el.querySelectorAll<HTMLElement>('[data-slot="reply-bar"]'));
const bodyRows = () => screen.getAllByRole('row').slice(1);

describe('Done companies', () => {
  it('folds the four metrics into one bordered strip, not four boxes', async () => {
    await done();
    await metric('Referrals');
    const strip = document.querySelector('[data-slot="done-strip"]') as HTMLElement;
    expect(strip).toHaveClass('grid-cols-4', 'border', 'overflow-hidden', 'bg-card');
    const cells = within(strip).getAllByRole('group');
    expect(cells).toHaveLength(4);
    for (const cell of cells) expect(cell).not.toHaveClass('border');
    expect(cells[0]).toHaveClass('border-r');
    expect(cells[3]).toHaveClass('last:border-r-0');
  });

  it('shows the four metrics', async () => {
    await done();
    expect(within(await metric('Replies in November')).getByText('9')).toBeInTheDocument();
    expect(within(await metric('Reply rate')).getByText('6.4%')).toBeInTheDocument();
    expect(within(await metric('Reply rate')).getByText('9 replies from 140 first emails this month')).toBeInTheDocument();
    expect(within(await metric('Referrals')).getByText('2')).toBeInTheDocument();
    expect(within(await metric('Interviews')).getByText('3')).toBeInTheDocument();
    expect(within(await metric('Interviews')).queryByText(/still in process/)).toBeNull();
  });

  it('says "1 reply" when there is exactly one, with a percent once the sample is enough', async () => {
    await done(withMetrics((m) => ({ ...m, replyRate: { replies: 1, firstEmails: 40 } })));
    const rate = await metric('Reply rate');
    expect(within(rate).getByText('2.5%')).toBeInTheDocument();
    expect(within(rate).getByText('1 reply from 40 first emails this month')).toBeInTheDocument();
  });

  it('shows no percent below the minimum sample: the counts, and a plain line that says why', async () => {
    await done(withMetrics((m) => ({ ...m, replyRate: { replies: 1, firstEmails: 20 } })));
    const rate = await metric('Reply rate');
    expect(within(rate).getByText('1 of 20')).toBeInTheDocument();
    expect(within(rate).getByText('1 reply from 20 first emails this month')).toBeInTheDocument();
    expect(within(rate).getByText('Too few first emails for a percent yet.')).toBeInTheDocument();
    expect(rate.textContent).not.toMatch(/%/);
    expect(rate.innerHTML).not.toMatch(/-bad|destructive/);
  });

  it('the line is at 30 first emails: 29 shows counts, 30 shows a percent', async () => {
    const { unmount } = await done(withMetrics((m) => ({ ...m, replyRate: { replies: 3, firstEmails: 29 } })));
    expect((await metric('Reply rate')).textContent).not.toMatch(/%/);
    unmount();
    await done(withMetrics((m) => ({ ...m, replyRate: { replies: 3, firstEmails: 30 } })));
    expect(within(await metric('Reply rate')).getByText('10%')).toBeInTheDocument();
  });

  it('shows None yet, not 0%, when there were no first emails', async () => {
    await done(withMetrics((m) => ({ ...m, replyRate: { replies: 0, firstEmails: 0 } })));
    const rate = await metric('Reply rate');
    expect(within(rate).getByText('None yet')).toBeInTheDocument();
    expect(rate.textContent).not.toMatch(/%/);
    expect(within(rate).getByText('No first emails this month')).toBeInTheDocument();
  });

  it('draws eight bars in one foreground series, the current week solid, labelled with every count', async () => {
    await done();
    const img = await chart();
    expect(img).toHaveAccessibleName('Replies per week, last 8 weeks: 0, 1, 2, 1, 3, 4, 5, 4 so far');
    const all = bars(img);
    expect(all).toHaveLength(8);
    for (const bar of all) expect(bar.className).toMatch(/\bbg-foreground/);
    expect(all.map((b) => b.dataset.current)).toEqual(['false', 'false', 'false', 'false', 'false', 'false', 'false', 'true']);
    expect(all[7]?.className).toMatch(/\bbg-foreground(\s|$)/);
    for (const bar of all.slice(0, 7)) expect(bar.className).not.toMatch(/\bbg-foreground(\s|$)/);
    // Heights follow the counts: the tallest week (5) is full height, an empty week keeps a 2px stub.
    expect(all[6]?.style.height).toBe('100%');
    expect(all[0]?.style.height).toBe('2px');
  });

  it('gives each bar a tooltip with its week and count', async () => {
    const user = userEvent.setup();
    await done();
    const all = bars(await chart());
    const tips = [
      'Week of Sep 21: 0', 'Week of Sep 28: 1', 'Week of Oct 5: 2', 'Week of Oct 12: 1',
      'Week of Oct 19: 3', 'Week of Oct 26: 4', 'Week of Nov 2: 5', 'This week so far: 4',
    ];
    for (const [i, tip] of tips.entries()) {
      const bar = all[i];
      if (!bar) throw new Error(`no bar ${i}`);
      // The hover target is the full-height column around the bar, so a 2px zero week can be reached.
      const column = bar.parentElement;
      if (!column) throw new Error(`no column ${i}`);
      await user.hover(column);
      expect(await screen.findByRole('tooltip')).toHaveTextContent(tip);
      await user.keyboard('{Escape}');
      await waitFor(() => expect(screen.queryByRole('tooltip')).toBeNull());
    }
  });

  it('lists the four companies, the first three imported', async () => {
    await done();
    await screen.findByRole('cell', { name: 'Northbeam Labs' });
    const rows = bodyRows();
    expect(rows.map((r) => within(r).getAllByRole('cell')[0]?.textContent)).toEqual(['Crusoe', 'Retell AI', 'Distyl AI', 'Northbeam Labs']);
    for (const row of rows.slice(0, 3)) {
      expect(within(row).getByText('Imported')).toBeInTheDocument();
      expect(within(row).getByText('Not recorded')).toBeInTheDocument();
      expect(within(row).getByText('No note')).toBeInTheDocument();
    }
    const northbeam = rows[3];
    if (!northbeam) throw new Error('no fourth row');
    expect(within(northbeam).queryByText('Imported')).toBeNull();
    expect(within(northbeam).getByText('Referral')).toBeInTheDocument();
    expect(within(northbeam).getByText('Email to Kai Andersen, engineer')).toBeInTheDocument();
    expect(within(northbeam).getByText('Software Engineer, Early Career')).toBeInTheDocument();
    expect(within(northbeam).getByText('Kai submitted the referral Nov 6')).toBeInTheDocument();
    expect(within(rows[0] as HTMLElement).getByText('Interviewing')).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).getByText('Interviewed')).toBeInTheDocument();
  });

  it('opens the selected company with Enter', async () => {
    const client = new FixtureClient();
    await done(client);
    await screen.findByRole('cell', { name: 'Crusoe' });
    const crusoe = (await client.done()).companies.find((d) => d.company.name === 'Crusoe');
    fireEvent.keyDown(document.body, { key: 'j' });
    await waitFor(() => expect(bodyRows()[0]).toHaveAttribute('aria-selected', 'true'));
    fireEvent.keyDown(document.body, { key: 'Enter' });
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(`/companies/${crusoe?.company.id}`));
  });

  it('links each company name to its page', async () => {
    const client = new FixtureClient();
    await done(client);
    const northbeam = (await client.done()).companies.find((d) => d.company.name === 'Northbeam Labs');
    expect(await screen.findByRole('link', { name: 'Northbeam Labs' })).toHaveAttribute('href', `/companies/${northbeam?.company.id}`);
  });

  it('shows the empty state when nothing is done', async () => {
    await done(new FixtureClient(buildFixture('empty')));
    expect(await screen.findByText('Nothing done yet.')).toBeInTheDocument();
    expect(screen.getByText('A company lands here when you close it with Referral or Interview.')).toBeInTheDocument();
    expect(screen.queryByRole('table')).toBeNull();
  });
});
