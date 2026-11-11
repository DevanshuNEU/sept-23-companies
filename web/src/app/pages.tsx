import { lazy } from 'react';
import { Navigate, useLocation } from 'react-router';

// Each view is its own chunk. The shell renders at once and shows a skeleton while a view loads.
// Kept apart from routes.tsx so this file exports only components (fast refresh needs that).
export const OutboxPage = lazy(() => import('@/routes/outbox/index'));
export const LinkedInPage = lazy(() => import('@/routes/linkedin/index'));
export const NeedsYouPage = lazy(() => import('@/routes/needs-you/index'));
export const NoGoPage = lazy(() => import('@/routes/no-go/index'));
export const CompanyPage = lazy(() => import('@/routes/company/index'));
export const DonePage = lazy(() => import('@/routes/done/index'));
export const AppliedPage = lazy(() => import('@/routes/applied/index'));
export const ProgressPage = lazy(() => import('@/routes/progress/index'));
export const BrowsePage = lazy(() => import('@/routes/browse/index'));
export const WinsPage = lazy(() => import('@/routes/wins/index'));
export const PipelinePage = lazy(() => import('@/routes/pipeline/index'));

/**
 * Element for child paths the parent view reads itself (`/outbox/:touchId`, `/no-go/people`).
 * Renders nothing; giving the child a component keeps React Router from warning about an empty leaf.
 */
export function ParentReads() {
  return null;
}

/** `/`, the old `/today` and `/proposals`, and unknown paths open the job list, keeping the query (`?scenario=`). */
export function ToJobs() {
  const { search } = useLocation();
  return <Navigate to={{ pathname: '/browse', search }} replace />;
}
