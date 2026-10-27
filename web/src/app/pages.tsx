import { lazy } from 'react';
import { Navigate, useLocation } from 'react-router';

// Each view is its own chunk. The shell renders at once and shows a skeleton while a view loads.
// Kept apart from routes.tsx so this file exports only components (fast refresh needs that).
export const TodayPage = lazy(() => import('@/routes/today/index'));
export const ProposalsPage = lazy(() => import('@/routes/proposals/index'));
export const OutboxPage = lazy(() => import('@/routes/outbox/index'));
export const LinkedInPage = lazy(() => import('@/routes/linkedin/index'));
export const NeedsYouPage = lazy(() => import('@/routes/needs-you/index'));
export const NoGoPage = lazy(() => import('@/routes/no-go/index'));
export const CompanyPage = lazy(() => import('@/routes/company/index'));
export const DonePage = lazy(() => import('@/routes/done/index'));
export const ProgressPage = lazy(() => import('@/routes/progress/index'));
export const BrowsePage = lazy(() => import('@/routes/browse/index'));
export const WinsPage = lazy(() => import('@/routes/wins/index'));

/**
 * Element for child paths the parent view reads itself (`/outbox/:touchId`, `/no-go/people`).
 * Renders nothing; giving the child a component keeps React Router from warning about an empty leaf.
 */
export function ParentReads() {
  return null;
}

/** `/` and unknown paths open Today, keeping the query (`?scenario=` in phase 1). */
export function ToToday() {
  const { search } = useLocation();
  return <Navigate to={{ pathname: '/today', search }} replace />;
}
