import type { RouteObject } from 'react-router';
import { AppShell } from '@/shell/app-shell';
import {
  CompanyPage,
  DonePage,
  LinkedInPage,
  NeedsYouPage,
  NoGoPage,
  OutboxPage,
  ParentReads,
  ToToday,
  TodayPage,
} from '@/app/pages';

/**
 * Every view route, relative to the shell. Views never edit this list. `/outbox/:touchId` and
 * `/no-go/people` are children that render nothing (`ParentReads`), so the page stays mounted when only
 * they change and reads them with `useParams()` and `useLocation()`.
 */
export const viewRoutes: RouteObject[] = [
  { path: 'today', element: <TodayPage /> },
  { path: 'outbox', element: <OutboxPage />, children: [{ path: ':touchId', element: <ParentReads /> }] },
  { path: 'linkedin', element: <LinkedInPage /> },
  { path: 'needs-you', element: <NeedsYouPage /> },
  { path: 'no-go', element: <NoGoPage />, children: [{ path: 'people', element: <ParentReads /> }] },
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
