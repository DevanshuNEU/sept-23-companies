import { lazy } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient } from '@tanstack/react-query';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { FixtureClient } from '@/api/fixture/client';
import { AppProviders } from '@/app/App';
import { routes } from '@/app/routes';
import { isChunkLoadError } from '@/app/chunk-error';

vi.mock('@/app/pages', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/app/pages')>()),
  OutboxPage: lazy(() =>
    Promise.reject(new TypeError('Failed to fetch dynamically imported module: http://127.0.0.1:5173/src/routes/outbox/index.tsx')),
  ),
  DonePage: function Broken(): never {
    throw new Error('rows is not iterable');
  },
}));

function renderRoute(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <AppProviders client={new FixtureClient()} queryClient={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  return router;
}

describe('route errors', () => {
  const realLocation = window.location;
  const reload = vi.fn();
  let consoleError: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    // React and React Router log an error they catch; the tests expect it.
    consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    reload.mockClear();
    Object.defineProperty(window, 'location', { configurable: true, value: { ...realLocation, reload } });
  });

  afterEach(() => {
    consoleError.mockRestore();
    Object.defineProperty(window, 'location', { configurable: true, value: realLocation });
  });

  it('shows the reload screen inside the shell when a view file cannot load, and Reload reloads', async () => {
    renderRoute('/outbox');
    expect(await screen.findByRole('heading', { name: 'This view could not load' })).toBeInTheDocument();
    expect(screen.getByText('The app could not reach its files. Nothing was changed.')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Views' })).toBeInTheDocument();
    expect(screen.queryByText(/Unexpected Application Error/)).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Go to Today' })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Reload' }));
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('does not reload by itself', async () => {
    renderRoute('/outbox');
    await screen.findByRole('heading', { name: 'This view could not load' });
    expect(reload).not.toHaveBeenCalled();
  });

  it('shows the generic screen with the message, Reload and Go to Today, and keeps the sidebar', async () => {
    const router = renderRoute('/done');
    expect(await screen.findByRole('heading', { name: 'Something went wrong in this view' })).toBeInTheDocument();
    expect(screen.getByText('Nothing was changed.')).toBeInTheDocument();
    expect(screen.getByText('rows is not iterable')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Views' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('link', { name: 'Go to Today' }));
    expect(router.state.location.pathname).toBe('/today');
    expect(await screen.findByRole('heading', { level: 1, name: 'Today' })).toBeInTheDocument();
  });
});

describe('isChunkLoadError', () => {
  it('matches the browser messages and the ChunkLoadError name only', () => {
    expect(isChunkLoadError(new TypeError('Failed to fetch dynamically imported module: x'))).toBe(true);
    expect(isChunkLoadError(new TypeError('Importing a module script failed.'))).toBe(true);
    expect(isChunkLoadError(Object.assign(new Error('x'), { name: 'ChunkLoadError' }))).toBe(true);
    expect(isChunkLoadError(new Error('rows is not iterable'))).toBe(false);
    expect(isChunkLoadError('Failed to fetch dynamically imported module')).toBe(false);
  });
});
