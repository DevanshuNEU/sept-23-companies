import { render, screen } from '@testing-library/react';
import { App } from '@/app/App';
import { createQueryClient } from '@/app/query-client';
import { ApiError } from '@/api';
import { FixtureClient } from '@/api/fixture/client';

describe('App', () => {
  it('sends / to Today inside the shell', async () => {
    render(<App client={new FixtureClient()} />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Today' })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/today');
    expect(screen.getByRole('navigation', { name: 'Views' })).toBeInTheDocument();
    expect(await screen.findByText('3 of 5')).toBeInTheDocument();
  });
});

describe('createQueryClient', () => {
  const retry = createQueryClient().getDefaultOptions().queries?.retry;
  if (typeof retry !== 'function') throw new Error('retry is a function');

  it('retries a network error at most twice', () => {
    const err = new ApiError('network', 'Cannot reach the outreach server');
    expect(retry(0, err)).toBe(true);
    expect(retry(1, err)).toBe(true);
    expect(retry(2, err)).toBe(false);
  });

  it('never retries an answer from the server', () => {
    for (const code of ['blocked', 'conflict', 'capacity', 'not_allowed', 'not_found'] as const) {
      expect(retry(0, new ApiError(code, 'no'))).toBe(false);
    }
  });

  it('treats an unknown error like a network error', () => {
    expect(retry(0, new TypeError('Failed to fetch'))).toBe(true);
    expect(retry(2, new TypeError('Failed to fetch'))).toBe(false);
  });
});
