import { waitFor } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import { queryKeys } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { renderWithApp } from '@/test/render';
import { PULSE_REFETCHES, useLivePulse } from '@/shell/use-pulse';

function Pulse() {
  useLivePulse();
  return null;
}

/** A fixture client whose pulse a test moves by hand, as a write from the terminal would. */
class Moving extends FixtureClient {
  rev = 7;
  calls = 0;
  override pulse() {
    this.calls += 1;
    return Promise.resolve({ revision: this.rev });
  }
}

function setup() {
  const client = new Moving();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
  renderWithApp(<Pulse />, { client, queryClient });
  const poll = () => queryClient.refetchQueries({ queryKey: queryKeys.pulse });
  return { client, invalidate, poll, queryClient };
}

describe('the pulse', () => {
  it('refetches the plan, the shell and Needs you when the number moves, and only then', async () => {
    const { client, invalidate, poll } = setup();
    await waitFor(() => expect(client.calls).toBe(1));
    expect(invalidate).not.toHaveBeenCalled(); // the first number is where the page starts, not a change

    await poll();
    expect(invalidate).not.toHaveBeenCalled(); // the same number: nothing was written

    client.rev = 9;
    await poll();
    await waitFor(() => expect(invalidate).toHaveBeenCalledTimes(PULSE_REFETCHES.length));
    expect(invalidate.mock.calls.map(([f]) => f?.queryKey)).toEqual([queryKeys.plan, queryKeys.shell, queryKeys.needsYou]);
  });

  it('polls every 5 seconds and not while the tab is hidden', async () => {
    const { client, queryClient } = setup();
    await waitFor(() => expect(client.calls).toBe(1));
    const query = queryClient.getQueryCache().find({ queryKey: queryKeys.pulse });
    const options = query?.options as { refetchInterval?: unknown; refetchIntervalInBackground?: unknown } | undefined;
    expect(options?.refetchInterval).toBe(5000);
    expect(options?.refetchIntervalInBackground).toBe(false);
  });
});
