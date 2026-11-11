import type { RouteObject } from 'react-router';
import { RootError, RouteError } from '@/app/route-error';
import { AppShell } from '@/shell/app-shell';
import {
  BrowsePage,
  CompanyPage,
  DonePage,
  LinkedInPage,
  NeedsYouPage,
  NoGoPage,
  OutboxPage,
  ParentReads,
  PipelinePage,
  ProgressPage,
  ToJobs,
  WinsPage,
} from '@/app/pages';

/**
 * Every view route, relative to the shell. Views never edit this list. `/outbox/:touchId` and
 * `/no-go/people` are children that render nothing (`ParentReads`), so the page stays mounted when only
 * they change and reads them with `useParams()` and `useLocation()`.
 */
export const viewRoutes: RouteObject[] = [
  { path: 'browse', element: <BrowsePage /> },
  { path: 'pipeline', element: <PipelinePage /> },
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
          // Knock opens on the job list (#195). Today and Proposals are gone; their old links land there too.
          { index: true, element: <ToJobs /> },
          { path: 'today', element: <ToJobs /> },
          { path: 'proposals', element: <ToJobs /> },
          ...viewRoutes,
          { path: '*', element: <ToJobs /> },
        ],
      },
    ],
  },
];
