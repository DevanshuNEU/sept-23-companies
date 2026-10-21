import type { RouteObject } from 'react-router';
import { RootError, RouteError } from '@/app/route-error';
import { AppShell } from '@/shell/app-shell';
import {
  CompanyPage,
  DonePage,
  LinkedInPage,
  NeedsYouPage,
  NoGoPage,
  OutboxPage,
  ParentReads,
  ProgressPage,
  ProposalsPage,
  ToToday,
  TodayPage,
  WinsPage,
} from '@/app/pages';

/**
 * Every view route, relative to the shell. Views never edit this list. `/outbox/:touchId` and
 * `/no-go/people` are children that render nothing (`ParentReads`), so the page stays mounted when only
 * they change and reads them with `useParams()` and `useLocation()`.
 */
export const viewRoutes: RouteObject[] = [
  { path: 'today', element: <TodayPage /> },
  { path: 'proposals', element: <ProposalsPage /> },
  { path: 'outbox', element: <OutboxPage />, children: [{ path: ':touchId', element: <ParentReads /> }] },
  { path: 'linkedin', element: <LinkedInPage /> },
  { path: 'needs-you', element: <NeedsYouPage /> },
  { path: 'no-go', element: <NoGoPage />, children: [{ path: 'people', element: <ParentReads /> }] },
  { path: 'companies/:companyId', element: <CompanyPage /> },
  { path: 'done', element: <DonePage /> },
  { path: 'progress', element: <ProgressPage /> },
  { path: 'wins', element: <WinsPage /> },
];

export const routes: RouteObject[] = [
  {
    path: '/',
    element: <AppShell />,
    // Last resort: the shell itself failed, so there is no sidebar to keep.
    errorElement: <RootError />,
    children: [
      {
        // A pathless layout: a failing view renders its error here, inside the shell's outlet.
        errorElement: <RouteError />,
        children: [
          { index: true, element: <ToToday /> },
          ...viewRoutes,
          { path: '*', element: <ToToday /> },
        ],
      },
    ],
  },
];
