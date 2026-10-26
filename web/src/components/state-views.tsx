import type { ReactNode } from 'react';
import type { UseQueryResult } from '@tanstack/react-query';
import { CircleAlert, TriangleAlert, type LucideIcon } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { formatLocalTime } from '@/lib/format';
import { cn } from '@/lib/utils';

/** Spec section 7: an icon, one bold line, one line on what to do next. */
export function EmptyState({
  icon: Icon,
  title,
  titleAs: Title = 'p',
  children,
  action,
}: {
  icon: LucideIcon;
  title: string;
  /** Full-screen states (a view that failed to load) name themselves with a heading. */
  titleAs?: 'p' | 'h1';
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
      <Icon aria-hidden className="size-8 text-muted-foreground" />
      <Title className="text-sm font-semibold">{title}</Title>
      {children ? <p className="max-w-sm text-sm text-muted-foreground">{children}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

const BANNER: Record<'warn' | 'bad', string> = {
  warn: 'border-warn-bd bg-warn-bg text-warn',
  bad: 'border-bad-bd bg-bad-bg text-bad',
};

/** An amber (stale) or red (error) alert that sits above content. */
export function StateBanner({
  tone,
  title,
  children,
  action,
}: {
  tone: 'warn' | 'bad';
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  const Icon = tone === 'bad' ? CircleAlert : TriangleAlert;
  return (
    <Alert className={cn(BANNER[tone])}>
      <Icon aria-hidden />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription className="text-current">
        {children ? <p>{children}</p> : null}
        {action}
      </AlertDescription>
    </Alert>
  );
}

/**
 * Loading, error, empty or content for one query. A refetch that fails keeps the last data on screen
 * under a red banner; an error with nothing loaded yet shows the banner with Retry.
 */
export function QueryState<T>({
  query,
  skeleton,
  isEmpty,
  empty,
  children,
}: {
  query: UseQueryResult<T>;
  skeleton: ReactNode;
  isEmpty?: (d: T) => boolean;
  empty: ReactNode;
  children: (d: T) => ReactNode;
}) {
  const { data } = query;

  if (data === undefined) {
    if (query.isError) {
      return (
        <StateBanner
          tone="bad"
          title="Cannot reach the Knock server"
          action={<Button variant="outline" size="sm" onClick={() => void query.refetch()}>Retry</Button>}
        >
          Nothing was changed.
        </StateBanner>
      );
    }
    return <>{skeleton}</>;
  }

  const body = isEmpty?.(data) ? empty : children(data);
  if (!query.isError) return <>{body}</>;

  return (
    <div className="flex flex-col gap-4">
      <StateBanner tone="bad" title="Cannot reach the Knock server">
        {`Nothing was changed. Showing what loaded at ${formatLocalTime(new Date(query.dataUpdatedAt).toISOString())}.`}
      </StateBanner>
      {body}
    </div>
  );
}
