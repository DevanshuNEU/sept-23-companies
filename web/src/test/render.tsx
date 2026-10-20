import type { ReactElement } from 'react';
import { render, type RenderResult } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { createMemoryRouter, RouterProvider, type RouteObject } from 'react-router';
import type { OutreachClient } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { AppProviders } from '@/app/App';
import { viewRoutes } from '@/app/routes';

/** The app's route paths with no pages, so `ui` stays mounted while the URL moves and `useParams` works. */
function paths(routes: RouteObject[]): RouteObject[] {
  return routes.map((r): RouteObject => (r.index ? { index: true } : { path: r.path, children: r.children ? paths(r.children) : undefined }));
}

/**
 * Renders `ui` inside every app provider and a memory router at `route` (default `/today`). `ui` is
 * the layout of every route, so it renders for any path; render `<AppShell />` in it for the shell.
 * Each call gets a fresh QueryClient with retries off and, unless given one, a fresh FixtureClient.
 */
export function renderWithApp(
  ui: ReactElement,
  opts: { route?: string; client?: OutreachClient } = {},
): RenderResult & { client: OutreachClient } {
  const client = opts.client ?? new FixtureClient();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(
    [{ path: '/', element: ui, children: [{ index: true }, ...paths(viewRoutes), { path: '*' }] }],
    { initialEntries: [opts.route ?? '/today'] },
  );
  const result = render(
    <AppProviders client={client} queryClient={queryClient}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  return Object.assign(result, { client });
}
