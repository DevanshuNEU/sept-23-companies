import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Inbox } from 'lucide-react';
import type { UseQueryResult } from '@tanstack/react-query';
import { EmptyState, QueryState, StateBanner } from '@/components/state-views';
import { StatusBadge } from '@/components/status-badge';

type Q = UseQueryResult<string[]>;

// QueryState reads status, data, dataUpdatedAt, refetch. A partial result is enough.
function query(over: Partial<Q>): Q {
  return { data: undefined, isError: false, isPending: true, dataUpdatedAt: 0, refetch: vi.fn(), ...over } as unknown as Q;
}

function renderState(q: Q, isEmpty?: (d: string[]) => boolean) {
  return render(
    <QueryState
      query={q}
      skeleton={<div>loading rows</div>}
      isEmpty={isEmpty}
      empty={<EmptyState icon={Inbox} title="No drafts to review">Paste people to start.</EmptyState>}
    >
      {(d) => <ul>{d.map((x) => <li key={x}>{x}</li>)}</ul>}
    </QueryState>,
  );
}

describe('QueryState', () => {
  it('shows the skeleton while loading with no data', () => {
    renderState(query({}));
    expect(screen.getByText('loading rows')).toBeInTheDocument();
  });

  it('renders the children once there is data', () => {
    renderState(query({ data: ['Graphite'], isPending: false }));
    expect(screen.getByText('Graphite')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByText('loading rows')).not.toBeInTheDocument();
  });

  it('shows the empty state when isEmpty says so', () => {
    renderState(query({ data: [], isPending: false }), (d) => d.length === 0);
    expect(screen.getByText('No drafts to review')).toBeInTheDocument();
    expect(screen.getByText('Paste people to start.')).toBeInTheDocument();
  });

  it('QueryState shows the last data with an error banner when a refetch fails', () => {
    // 2026-11-10T15:14:00Z is 10:14 in Boston.
    const at = Date.parse('2026-11-10T15:14:00Z');
    renderState(query({ data: ['Graphite'], isError: true, isPending: false, dataUpdatedAt: at }));
    expect(screen.getByText('Graphite')).toBeInTheDocument();
    const banner = screen.getByRole('alert');
    expect(banner).toHaveTextContent('Showing what loaded at 10:14');
    expect(banner).toHaveTextContent('Cannot reach the Knock server');
  });

  it('keeps the empty state under the banner when the old data was empty', () => {
    renderState(query({ data: [], isError: true, isPending: false, dataUpdatedAt: 0 }), (d) => d.length === 0);
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText('No drafts to review')).toBeInTheDocument();
  });

  it('with an error and no data shows the banner and a Retry that refetches', async () => {
    const refetch = vi.fn();
    renderState(query({ isError: true, isPending: false, refetch }));
    expect(screen.getByRole('alert')).toHaveTextContent('Cannot reach the Knock server');
    expect(screen.queryByText('loading rows')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });
});

describe('StateBanner', () => {
  it('shows the title, text and action', () => {
    render(
      <StateBanner tone="warn" title="Boards last read 6 days ago" action={<button>Open runs</button>}>
        Thursday's read did not run.
      </StateBanner>,
    );
    expect(screen.getByRole('alert')).toHaveTextContent("Boards last read 6 days ago");
    expect(screen.getByText("Thursday's read did not run.")).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open runs' })).toBeInTheDocument();
  });
});

describe('EmptyState', () => {
  it('shows a bold line, a next step and an optional action', () => {
    render(
      <EmptyState icon={Inbox} title="Nobody to reach" action={<button>Add people</button>}>
        People past the email budget land here.
      </EmptyState>,
    );
    expect(screen.getByText('Nobody to reach')).toBeInTheDocument();
    expect(screen.getByText('People past the email budget land here.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add people' })).toBeInTheDocument();
  });
});

describe('StatusBadge', () => {
  it('shows the word with the tone variant', () => {
    render(<StatusBadge status={{ label: 'Blocked', tone: 'bad' }} />);
    const badge = screen.getByText('Blocked');
    expect(badge).toHaveAttribute('data-variant', 'bad');
  });

  it('adds a dot only when asked', () => {
    const { rerender, container } = render(<StatusBadge status={{ label: 'Sent', tone: 'idle' }} />);
    expect(container.querySelector('[data-slot="status-dot"]')).toBeNull();
    rerender(<StatusBadge status={{ label: 'Sent', tone: 'idle' }} dot />);
    expect(container.querySelector('[data-slot="status-dot"]')).not.toBeNull();
  });

  it('draws the icon when the status has one', () => {
    const { container } = render(<StatusBadge status={{ label: 'Failed', tone: 'bad', icon: 'warn' }} />);
    expect(container.querySelector('svg')).not.toBeNull();
  });
});
