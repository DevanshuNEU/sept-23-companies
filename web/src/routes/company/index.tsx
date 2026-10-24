import { useState } from 'react';
import { useParams } from 'react-router';
import { Building2 } from 'lucide-react';
import { ApiError, pollMs, queryKeys, usePageClient, type CompanyView } from '@/api';
import { CloseCompanyDialog } from '@/components/close-company-dialog';
import { EmptyState, QueryState } from '@/components/state-views';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useHotkeys } from '@/keyboard/hotkeys';
import { useUrlState } from '@/lib/url-state';
import { useViewQuery } from '@/pending/pending';
import { CompanyHeader, FactsStrip } from './company-header';
import { ReopenDialog, ResumeDialog } from './company-dialogs';
import { companyActions } from './company-status';
import { TabBoundary } from './tab-boundary';
import { Emails } from './tabs/emails';
import { Evidence } from './tabs/evidence';
import { History } from './tabs/history';
import { Overview } from './tabs/overview';
import { People } from './tabs/people';
import { Roles } from './tabs/roles';
import { useCompanyActions } from './use-company-actions';

const TABS = [
  { id: 'overview', label: 'Overview', count: null },
  { id: 'roles', label: 'Roles', count: (v: CompanyView) => v.postings.length },
  { id: 'people', label: 'People', count: (v: CompanyView) => v.people.length },
  { id: 'emails', label: 'Emails', count: (v: CompanyView) => v.touches.length },
  { id: 'evidence', label: 'Evidence', count: (v: CompanyView) => v.evidence.length },
  { id: 'history', label: 'History', count: (v: CompanyView) => v.events.length },
] as const;
type TabId = (typeof TABS)[number]['id'];

const isTab = (v: string): v is TabId => TABS.some((t) => t.id === v);

function LoadingSkeleton() {
  return (
    <div aria-busy="true" className="grid gap-4">
      <Skeleton className="h-8 w-64" />
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-64 w-full" />
    </div>
  );
}

/** Everything known about one company: facts, six tabs, and what he can do with it (design 5.6). */
export default function CompanyPage() {
  const { companyId } = useParams();
  const id = Number(companyId);
  const client = usePageClient('company');
  const query = useViewQuery(queryKeys.company(id), () => client.company(id), { pollMs: pollMs.default });
  const view = query.data;
  const actions = useCompanyActions(view);
  const can = view ? companyActions(view.company) : null;

  const [rawTab, setTab] = useUrlState('tab', 'overview', { push: true });
  const tab: TabId = isTab(rawTab) ? rawTab : 'overview';
  const [closing, setClosing] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [reopening, setReopening] = useState(false);

  useHotkeys([
    ...TABS.map((t, i) => ({ keys: String(i + 1), label: `${t.label} tab`, group: 'Company page', enabled: view !== undefined, run: () => setTab(t.id) })),
    { keys: 'c', label: 'Close with a reason', group: 'Company page', enabled: can?.close === true, run: () => setClosing(true) },
    { keys: 'p', label: 'Pause the company', group: 'Company page', enabled: can?.pause === true, run: actions.pause },
    { keys: 'r', label: 'Resume the company', group: 'Company page', enabled: can?.resume === true, run: () => setResuming(true) },
    { keys: 'y', label: 'Copy the role title', group: 'Company page', enabled: view?.role != null, run: actions.copyRole },
  ]);

  const notFound = view === undefined && query.error instanceof ApiError && query.error.code === 'not_found';

  return (
    <div className="max-w-[1360px] px-6 pt-5 pb-12">
      {notFound ? (
        <>
          <h1 className="text-2xl font-semibold tracking-tight">Company</h1>
          <EmptyState icon={Building2} title="No company with this id">
            Check the link, or search for the company with the search box in the sidebar.
          </EmptyState>
        </>
      ) : (
        <QueryState query={query} skeleton={<LoadingSkeleton />} empty={null}>
          {(v) => (
            <>
              <CompanyHeader
                view={v}
                handlers={{ onPause: actions.pause, onResume: () => setResuming(true), onClose: () => setClosing(true), onReopen: () => setReopening(true) }}
              />
              <FactsStrip view={v} />
              <Tabs value={tab} onValueChange={(t) => isTab(t) && setTab(t)}>
                <TabsList aria-label="Company sections" className="mb-2">
                  {TABS.map((t) => (
                    <TabsTrigger key={t.id} value={t.id}>
                      {t.label}
                      {t.count ? <span className="text-xs text-muted-foreground tabular-nums">{t.count(v)}</span> : null}
                    </TabsTrigger>
                  ))}
                </TabsList>
                {TABS.map((t) => (
                  <TabsContent key={t.id} value={t.id}>
                    <TabBoundary name={t.label} onRetry={() => void query.refetch()}>
                      {t.id === 'overview' ? (
                        <Overview view={v} onCopyRole={actions.copyRole} onOpenHistory={() => setTab('history')} />
                      ) : t.id === 'roles' ? (
                        <Roles view={v} />
                      ) : t.id === 'people' ? (
                        <People view={v} />
                      ) : t.id === 'emails' ? (
                        <Emails view={v} />
                      ) : t.id === 'evidence' ? (
                        <Evidence view={v} />
                      ) : (
                        <History view={v} />
                      )}
                    </TabBoundary>
                  </TabsContent>
                ))}
              </Tabs>
              <CloseCompanyDialog company={v.company} open={closing} onOpenChange={setClosing} onClose={actions.close} />
              <ResumeDialog view={v} open={resuming} onOpenChange={setResuming} onResume={actions.resume} />
              <ReopenDialog name={v.company.name} open={reopening} onOpenChange={setReopening} onReopen={actions.reopen} />
            </>
          )}
        </QueryState>
      )}
    </div>
  );
}
