import { isRouteErrorResponse, Link, useLocation, useRouteError } from 'react-router';
import { TriangleAlert } from 'lucide-react';
import { EmptyState } from '@/components/state-views';
import { Button } from '@/components/ui/button';
import { isChunkLoadError } from './chunk-error';

function errorText(error: unknown): string {
  if (isRouteErrorResponse(error)) return `${error.status} ${error.statusText}`.trim();
  if (error instanceof Error) return error.message;
  return String(error);
}

/**
 * Replaces React Router's raw error page. Says what happened, that nothing was changed, and what to do.
 * Reload is a button the user presses: the app never reloads by itself, so a broken chunk cannot loop.
 */
function ErrorScreen() {
  const error = useRouteError();
  const { search } = useLocation();
  const reload = (
    <Button size="sm" onClick={() => window.location.reload()}>
      Reload
    </Button>
  );

  if (isChunkLoadError(error)) {
    return (
      <EmptyState icon={TriangleAlert} titleAs="h1" title="This view could not load" action={reload}>
        The app could not reach its files. Nothing was changed.
      </EmptyState>
    );
  }
  return (
    <EmptyState
      icon={TriangleAlert}
      titleAs="h1"
      title="Something went wrong in this view"
      action={
        <div className="flex flex-col items-center gap-3">
          <div className="flex gap-2">
            {reload}
            <Button asChild variant="outline" size="sm">
              <Link to={{ pathname: '/today', search }}>Go to Today</Link>
            </Button>
          </div>
          <p className="max-w-lg break-words font-mono text-xs text-muted-foreground">{errorText(error)}</p>
        </div>
      }
    >
      Nothing was changed.
    </EmptyState>
  );
}

/** Sits inside the shell's outlet, so the sidebar and top bar stay usable. */
export function RouteError() {
  return <ErrorScreen />;
}

/** Last resort, when the shell itself fails: the same screen, centered on its own. */
export function RootError() {
  return (
    <div className="flex min-h-svh items-center justify-center">
      <ErrorScreen />
    </div>
  );
}
