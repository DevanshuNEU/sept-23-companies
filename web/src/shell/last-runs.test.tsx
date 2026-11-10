import { render, screen, within } from '@testing-library/react';
import type { JobRunSummary } from '@/api';
import { LastRuns } from './last-runs';

const run = (o: Partial<JobRunSummary> & Pick<JobRunSummary, 'job'>): JobRunSummary => ({
  startedAt: '2026-11-06T11:30:00Z', finishedAt: '2026-11-06T11:31:00Z', status: 'ok', error: '', counts: [],
  expectedNextAt: null, late: false, ...o,
});

const FIND_BOARDS = run({
  job: 'find_boards', status: 'partial', error: '11 careers checks errored (nothing concluded, asked again next run)',
  counts: [{ label: 'Careers checked', value: 14 }, { label: 'Adapter unknown', value: 0 }, { label: 'Conflicts', value: 38 }],
});
const PLAN = run({ job: 'plan', counts: [{ label: 'Capacity', value: 8 }, { label: 'Rows', value: 8 }] });
const PREPARE = run({
  job: 'prepare', status: 'partial', late: true, expectedNextAt: '2026-10-27T09:30:00Z', error: 'company 92: unreadable reply',
});

describe('LastRuns', () => {
  it('puts the runs that need a look first and names why', () => {
    render(<LastRuns jobs={[PLAN, FIND_BOARDS, PREPARE]} />);
    const look = screen.getByRole('list', { name: 'Needs a look' });
    expect(within(look).getAllByRole('listitem').map((li) => li.getAttribute('aria-label'))).toEqual(['Find boards', 'Prepare']);
    expect(within(look).getByText('11 careers checks errored (nothing concluded, asked again next run)')).toBeInTheDocument();
    expect(within(look).getByText('Late')).toBeInTheDocument();
    expect(within(look).getByText(/^Expected /)).toBeInTheDocument();
  });

  it('shows a clean run as one quiet line with no counts and no badge', () => {
    render(<LastRuns jobs={[PLAN, FIND_BOARDS]} />);
    const fine = screen.getByRole('list', { name: 'Ran as expected' });
    const row = within(fine).getByRole('listitem', { name: 'Plan' });
    expect(row).toHaveTextContent(/^Plan\s*Fri Nov 6, 06:30$/);
    expect(within(row).queryByText(/Capacity/)).toBeNull();
    expect(row.querySelector('[data-slot="badge"]')).toBeNull();
  });

  it('keeps the counts behind a toggle and leaves zeros out', () => {
    render(<LastRuns jobs={[FIND_BOARDS]} />);
    const row = screen.getByRole('listitem', { name: 'Find boards' });
    const toggle = within(row).getByText('Counts');
    expect(toggle.closest('details')).not.toHaveAttribute('open');
    expect(row).toHaveTextContent('Careers checked 14, Conflicts 38');
    expect(row).not.toHaveTextContent('Adapter unknown');
  });

  it('leaves out the empty list when every run is fine, and says so when nothing ran', () => {
    const { rerender } = render(<LastRuns jobs={[PLAN]} />);
    expect(screen.queryByRole('list', { name: 'Needs a look' })).toBeNull();
    rerender(<LastRuns jobs={[]} />);
    expect(screen.getByText('Nothing has run yet')).toBeInTheDocument();
  });

  it('says Running for a run in progress', () => {
    render(<LastRuns jobs={[run({ job: 'sender', status: 'running', finishedAt: null })]} />);
    expect(screen.getByRole('listitem', { name: 'Sender' })).toHaveTextContent('Running');
  });
});
