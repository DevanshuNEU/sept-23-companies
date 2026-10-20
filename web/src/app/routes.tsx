import { lazy } from 'react';
import { Navigate, useLocation, type RouteObject } from 'react-router';
import { AppShell } from '@/shell/app-shell';

// Each view is its own chunk. The shell renders at once and shows a skeleton while a view loads.
const TodayPage = lazy(() => import('@/routes/today/index'));
const OutboxPage = lazy(() => import('@/routes/outbox/index'));
const LinkedInPage = lazy(() => import('@/routes/linkedin/index'));
const NeedsYouPage = lazy(() => import('@/routes/needs-you/index'));
const NoGoPage = lazy(() => import('@/routes/no-go/index'));
const CompanyPage = lazy(() => import('@/routes/company/index'));
const DonePage = lazy(() => import('@/routes/done/index'));

/** `/` and unknown paths open Today, keeping the query (`?scenario=` in phase 1). */
function ToToday() {
  const { search } = useLocation();
  return <Navigate to={{ pathname: '/today', search }} replace />;
}

/**
 * Every view route, relative to the shell. Views never edit this list. `/outbox/:touchId` and
 * `/no-go/people` are children with no element, so the page stays mounted when only they change and
 * reads them with `useParams()` and `useLocation()`.
 */
export const viewRoutes: RouteObject[] = [
  { path: 'today', element: <TodayPage /> },
  { path: 'outbox', element: <OutboxPage />, children: [{ path: ':touchId' }] },
  { path: 'linkedin', element: <LinkedInPage /> },
  { path: 'needs-you', element: <NeedsYouPage /> },
  { path: 'no-go', element: <NoGoPage />, children: [{ path: 'people' }] },
  { path: 'companies/:companyId', element: <CompanyPage /> },
  { path: 'done', element: <DonePage /> },
];

export const routes: RouteObject[] = [
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <ToToday /> },
      ...viewRoutes,
      { path: '*', element: <ToToday /> },
    ],
  },
];
