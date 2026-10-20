import { render, screen, within } from '@testing-library/react';
import { ConfidenceMeter } from '@/components/confidence-meter';
import { FollowupTrack } from '@/components/followup-track';
import { LinkedInSteps } from '@/components/linkedin-steps';
import { RampLadder } from '@/components/ramp-ladder';
import { RunStrip } from '@/components/run-strip';
import { Timeline } from '@/components/timeline';

const ASOF = '2026-11-10T15:14:00Z'; // Tue Nov 10, 10:14 in Boston
const LEVELS = [20, 30, 40, 50, 60, 70, 80, 90, 100];

describe('RampLadder', () => {
  it('labels the level, the limit and today', () => {
    render(<RampLadder levels={LEVELS} level={2} sent={20} limit={30} />);
    expect(screen.getByRole('img', { name: 'Limit level 2 of 9: 30 a day. Today 20 sent.' })).toBeInTheDocument();
  });

  it('draws nine bars from 40% to 100% tall, the current one filled by today\'s sent share', () => {
    const { container } = render(<RampLadder levels={LEVELS} level={2} sent={20} limit={30} />);
    const bars = container.querySelectorAll<HTMLElement>('[data-step]');
    expect(bars).toHaveLength(9);
    expect(bars[0]).toHaveStyle({ height: '40%' });
    expect(bars[1]).toHaveStyle({ height: '47.5%' });
    expect(bars[8]).toHaveStyle({ height: '100%' });
    expect(bars[0]).toHaveAttribute('data-step', 'past');
    expect(bars[1]).toHaveAttribute('data-step', 'now');
    expect(bars[2]).toHaveAttribute('data-step', 'next');
    const fill = bars[1]?.querySelector('i');
    expect(fill).toHaveStyle({ height: '67%' });
    expect(bars[0]?.querySelector('i')).toBeNull();
  });

  it('caps the fill at 100% and handles a zero limit', () => {
    const { container, rerender } = render(<RampLadder levels={LEVELS} level={1} sent={25} limit={20} />);
    expect(container.querySelector('[data-step="now"] i')).toHaveStyle({ height: '100%' });
    rerender(<RampLadder levels={LEVELS} level={1} sent={0} limit={0} />);
    expect(container.querySelector('[data-step="now"] i')).toHaveStyle({ height: '0%' });
  });

  it('has a mini size', () => {
    render(<RampLadder levels={LEVELS} level={3} sent={0} limit={40} mini />);
    expect(screen.getByRole('img')).toHaveAttribute('data-mini', 'true');
  });
});

describe('ConfidenceMeter', () => {
  it.each([
    ['confirmed', 3, 'High confidence'],
    ['estimate', 2, 'Medium confidence'],
    ['guess', 1, 'Guess'],
  ] as const)('%s lights %i bars', (confidence, bars, label) => {
    const { container } = render(<ConfidenceMeter confidence={confidence} />);
    expect(screen.getByRole('img', { name: label })).toBeInTheDocument();
    expect(container.querySelectorAll('[data-on="true"]')).toHaveLength(bars);
    expect(container.querySelectorAll('[data-on]')).toHaveLength(3);
  });
});

describe('RunStrip', () => {
  it('labels each read in order', () => {
    render(
      <RunStrip
        reads={[
          { at: '2026-10-19T12:00:00Z', result: 'ok' },
          { at: '2026-10-22T12:00:00Z', result: 'error' },
          { at: '2026-10-26T12:00:00Z', result: 'error' },
        ]}
      />,
    );
    expect(screen.getByRole('img', { name: 'ok, error, error' })).toBeInTheDocument();
  });

  it('tells apart ok, empty and error by data attribute', () => {
    const { container } = render(
      <RunStrip
        reads={[
          { at: '2026-10-19T12:00:00Z', result: 'ok' },
          { at: '2026-10-22T12:00:00Z', result: 'empty' },
          { at: '2026-10-26T12:00:00Z', result: 'error' },
        ]}
      />,
    );
    expect([...container.querySelectorAll('i')].map((i) => i.getAttribute('data-result'))).toEqual(['ok', 'empty', 'error']);
  });
});

describe('LinkedInSteps', () => {
  it('reads the latest step with its day', () => {
    render(<LinkedInSteps steps={[{ step: 'request_sent', at: '2026-11-06T15:00:00Z' }]} asOf={ASOF} />);
    expect(screen.getByText('Requested Fri Nov 6')).toBeInTheDocument();
  });

  it('lights the steps reached and names the last one', () => {
    const { container } = render(
      <LinkedInSteps
        steps={[
          { step: 'request_sent', at: '2026-11-06T15:00:00Z' },
          { step: 'accepted', at: '2026-11-08T15:00:00Z' },
          { step: 'message_sent', at: '2026-11-09T15:00:00Z' },
        ]}
        asOf={ASOF}
      />,
    );
    expect(screen.getByText('Messaged Mon Nov 9')).toBeInTheDocument();
    expect(container.querySelectorAll('[data-on="true"]')).toHaveLength(3 + 2);
    expect(screen.getByRole('img', { name: 'Messaged Mon Nov 9' })).toBeInTheDocument();
  });

  it('says Not started with no steps, and Replied at the end', () => {
    const { rerender } = render(<LinkedInSteps steps={[]} asOf={ASOF} />);
    expect(screen.getByText('Not started')).toBeInTheDocument();
    rerender(
      <LinkedInSteps
        steps={[
          { step: 'request_sent', at: '2026-11-02T15:00:00Z' },
          { step: 'accepted', at: '2026-11-03T15:00:00Z' },
          { step: 'message_sent', at: '2026-11-04T15:00:00Z' },
          { step: 'replied', at: '2026-11-10T14:00:00Z' },
        ]}
        asOf={ASOF}
      />,
    );
    expect(screen.getByText('Replied today')).toBeInTheDocument();
  });
});

describe('FollowupTrack', () => {
  const followups = [
    { seq: 1 as const, dueOn: '2026-11-04', sentAt: '2026-11-04T15:00:00Z', state: 'sent' as const },
    { seq: 2 as const, dueOn: '2026-11-10', sentAt: null, state: 'drafted' as const },
    { seq: 3 as const, dueOn: '2026-11-20', sentAt: null, state: 'planned' as const },
  ];

  it('shows sent steps, today as due today with the day, and a later due date', () => {
    render(<FollowupTrack firstSentAt="2026-10-30T14:00:00Z" followups={followups} asOf={ASOF} />);
    const track = screen.getByRole('group', { name: 'Follow-up schedule' });
    expect(within(track).getByText('First email')).toBeInTheDocument();
    expect(within(track).getByText('Sent Fri Oct 30')).toBeInTheDocument();
    expect(within(track).getByText('Sent Wed Nov 4')).toBeInTheDocument();
    expect(within(track).getByText('Due today, Tue Nov 10')).toBeInTheDocument();
    expect(within(track).getByText('Follow-up 3, polite close')).toBeInTheDocument();
    expect(within(track).getByText('Due Fri Nov 20')).toBeInTheDocument();
  });

  it('marks done and the next due step', () => {
    const { container } = render(<FollowupTrack firstSentAt="2026-10-30T14:00:00Z" followups={followups} asOf={ASOF} />);
    expect([...container.querySelectorAll('[data-step]')].map((e) => e.getAttribute('data-step'))).toEqual([
      'done',
      'done',
      'now',
      'next',
    ]);
  });

  it('copes with nothing sent and a missing follow-up', () => {
    render(<FollowupTrack firstSentAt={null} followups={[]} asOf={ASOF} />);
    expect(screen.getByText('Not sent yet')).toBeInTheDocument();
    expect(screen.getAllByText('Not scheduled')).toHaveLength(3);
  });
});

describe('Timeline', () => {
  it('lists items with a tone dot, the time and the tag', () => {
    const { container } = render(
      <Timeline
        items={[
          { id: 1, at: '2026-11-10T14:12:00Z', text: 'Priya Natarajan replied.', tone: 'ok' },
          { id: 'b', at: '2026-10-21T14:00:00Z', text: 'Stage Referral asked', tone: 'idle', tag: 'Imported' },
        ]}
      />,
    );
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByText('Priya Natarajan replied.')).toBeInTheDocument();
    expect(screen.getByText('Imported')).toBeInTheDocument();
    expect(screen.getByText('Tue Nov 10, 09:12 ET')).toBeInTheDocument();
    expect([...container.querySelectorAll('[data-tone]')].map((e) => e.getAttribute('data-tone'))).toEqual(['ok', 'idle']);
  });
});
