import { render, screen } from '@testing-library/react';
import { App } from '@/app/App';
import { createQueryClient } from '@/app/query-client';
import { ApiError } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { routes, viewRoutes } from '@/app/routes';

describe('App', () => {
  it('sends / to Today inside the shell', async () => {
    render(<App client={new FixtureClient()} />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Today' })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/today');
    expect(screen.getByRole('navigation', { name: 'Views' })).toBeInTheDocument();
    expect(await screen.findByText('2 of 7')).toBeInTheDocument();
  });
});

describe('App routes for results', () => {
  it.each([['/progress', 'Progress'], ['/wins', 'Wins']])('opens %s with its title inside the shell', async (path, title) => {
    window.history.pushState({}, '', path);
    render(<App client={new FixtureClient()} />);
    expect(await screen.findByRole('heading', { level: 1, name: title })).toBeInTheDocument();
    expect(screen.getByText('Coming in this branch')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Results' })).toBeInTheDocument();
    window.history.pushState({}, '', '/');
  });
});

describe('route table', () => {
  const pathsOf = (list: typeof routes, parent = ''): string[] =>
    list.flatMap((r) => {
      const here = r.path ? `${parent}/${r.path}`.replace(/\/+/g, '/') : parent;
      return [...(r.path ? [here] : []), ...(r.children ? pathsOf(r.children, here) : [])];
    });

  it('has no duplicate paths', () => {
    const all = pathsOf(routes);
    expect(new Set(all).size).toBe(all.length);
  });

  it('has Progress and Wins as views', () => {
    const paths = viewRoutes.map((r) => r.path);
    expect(paths).toContain('progress');
    expect(paths).toContain('wins');
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
