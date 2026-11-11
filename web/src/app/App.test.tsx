import { render, screen } from '@testing-library/react';
import { App } from '@/app/App';
import { createQueryClient, retryDelay } from '@/app/query-client';
import { ApiError } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { routes, viewRoutes } from '@/app/routes';

describe('App', () => {
  it('root opens Jobs inside the shell', async () => {
    window.history.pushState({}, '', '/');
    render(<App client={new FixtureClient()} />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Jobs' })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/browse');
    expect(screen.getByRole('navigation', { name: 'Views' })).toBeInTheDocument();
  });

  it.each(['/today', '/proposals', '/nowhere'])('%s redirects to Jobs and keeps the query', async (path) => {
    window.history.pushState({}, '', `${path}?scenario=queue`);
    render(<App client={new FixtureClient()} />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Jobs' })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/browse');
    expect(window.location.search).toBe('?scenario=queue');
    window.history.pushState({}, '', '/');
  });
});

describe('App routes for results', () => {
  it.each([['/progress', 'Progress'], ['/wins', 'Wins']])('opens %s with its title inside the shell', async (path, title) => {
    window.history.pushState({}, '', path);
    render(<App client={new FixtureClient()} />);
    expect(await screen.findByRole('heading', { level: 1, name: title })).toBeInTheDocument();
    expect(screen.queryByText('Coming in this branch')).not.toBeInTheDocument();
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

  it('has no Today or Proposals view', () => {
    const paths = viewRoutes.map((r) => r.path);
    expect(paths).not.toContain('today');
    expect(paths).not.toContain('proposals');
  });

  it('has Progress and Wins as views', () => {
    const paths = viewRoutes.map((r) => r.path);
    expect(paths).toContain('progress');
    expect(paths).toContain('wins');
  });
});

describe('createQueryClient', () => {
  const queries = createQueryClient().getDefaultOptions().queries;
  const retry = queries?.retry;
  if (typeof retry !== 'function') throw new Error('retry is a function');

  it('retries a read that got no answer once (offline, timed out, server busy)', () => {
    const err = new ApiError('network', 'Cannot reach the Knock server');
    expect(retry(0, err)).toBe(true);
    expect(retry(1, err)).toBe(false);
  });

  it('never retries an answer from the server', () => {
    for (const code of ['blocked', 'conflict', 'capacity', 'not_allowed', 'not_found'] as const) {
      expect(retry(0, new ApiError(code, 'no'))).toBe(false);
    }
  });

  it('treats an unknown error like a network error', () => {
    expect(retry(0, new TypeError('Failed to fetch'))).toBe(true);
    expect(retry(1, new TypeError('Failed to fetch'))).toBe(false);
  });

  it('waits a few seconds before the retry, spread so tabs do not come back together', () => {
    expect(queries?.retryDelay).toBe(retryDelay);
    expect(retryDelay(0, new Error('x'), () => 0)).toBe(2000);
    expect(retryDelay(0, new Error('x'), () => 0.999)).toBeCloseTo(4000, -1);
    expect(retryDelay(5, new Error('x'), () => 0)).toBe(15000);
  });

  it('keeps an answer fresh for 10 seconds, so a remount or a refocus inside that reuses it', () => {
    expect(queries?.staleTime).toBe(10000);
  });
});
