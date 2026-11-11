import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { AppliedRow, AppliedView } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { renderWithApp } from '@/test/render';
import AppliedPage from './index';

/*
 * The Applied list (#218): every posting he marked applied, newest first, each with its job link, its company, the day
 * and whether the posting is still open.
 */

const ROWS: AppliedRow[] = [
  { postingId: 2, companyId: 20, companyName: 'Ember', title: 'Backend Engineer', url: 'https://jobs.example/ember/2', appliedOn: '2026-11-11', open: true },
  { postingId: 1, companyId: 10, companyName: 'Acme', title: 'Software Engineer, New Grad', url: 'https://jobs.example/acme/1', appliedOn: '2026-11-03', open: false },
  { postingId: 3, companyId: 30, companyName: 'Corvid', title: 'Platform Engineer', url: '', appliedOn: '2026-10-28', open: true },
];

function withRows(rows: AppliedRow[]) {
  class Changed extends FixtureClient {
    override async applied(): Promise<AppliedView> {
      return { asOf: '2026-11-11T15:00:00Z', rows };
    }
  }
  return new Changed();
}

async function open(rows = ROWS) {
  const view = renderWithApp(<AppliedPage />, { route: '/applied', client: withRows(rows) });
  await screen.findByRole('heading', { level: 1, name: 'Applied jobs' });
  return view;
}

const bodyRows = () => screen.getAllByRole('row').slice(1);

describe('the Applied page', () => {
  it('lists every application newest first, with the job link, the company, the day and the posting state', async () => {
    await open();
    await waitFor(() => expect(bodyRows()).toHaveLength(3));
    expect(screen.getByText('3 jobs you applied to, newest first.')).toBeInTheDocument();
    const [first, second] = bodyRows() as HTMLElement[];
    const link = within(first as HTMLElement).getByRole('link', { name: 'Backend Engineer' });
    expect(link).toHaveAttribute('href', 'https://jobs.example/ember/2');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'));
    expect(within(first as HTMLElement).getByRole('link', { name: 'Ember' })).toHaveAttribute('href', '/companies/20');
    expect(first).toHaveTextContent('Nov 11');
    expect(first).toHaveTextContent('Open');
    expect(second).toHaveTextContent('Nov 3');
    expect(second).toHaveTextContent('Closed');
  });

  it('shows a role with no link as plain text', async () => {
    await open();
    await waitFor(() => expect(bodyRows()).toHaveLength(3));
    const third = bodyRows()[2] as HTMLElement;
    expect(within(third).getByText('Platform Engineer').closest('a')).toBeNull();
  });

  it('a search narrows by company or role, and says so when nothing matches', async () => {
    const user = userEvent.setup();
    await open();
    await waitFor(() => expect(bodyRows()).toHaveLength(3));
    const box = screen.getByRole('searchbox', { name: 'Search by company or role' });
    await user.type(box, 'acme');
    await waitFor(() => expect(bodyRows()).toHaveLength(1));
    await user.clear(box);
    await user.type(box, 'platform');
    await waitFor(() => expect(bodyRows()).toHaveLength(1));
    await user.clear(box);
    await user.type(box, 'zzz');
    expect(await screen.findByText('No applications match your search.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Clear search' }));
    await waitFor(() => expect(bodyRows()).toHaveLength(3));
  });

  it('says how it fills up when there is nothing yet', async () => {
    await open([]);
    expect(await screen.findByText('No applications yet.')).toBeInTheDocument();
    expect(screen.getByText('Press Mark applied on a job and it stays here.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open Jobs' })).toHaveAttribute('href', '/browse');
  });

  it('counts one job in the singular', async () => {
    await open(ROWS.slice(0, 1));
    expect(await screen.findByText('1 job you applied to, newest first.')).toBeInTheDocument();
  });
});
