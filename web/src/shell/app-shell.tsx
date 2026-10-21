import { Suspense, useEffect, useRef, useState, type CSSProperties } from 'react';
import { Outlet, useLocation, useNavigate, useParams } from 'react-router';
import { Keyboard } from 'lucide-react';
import { pollMs, queryKeys, useClient } from '@/api';
import { Badge } from '@/components/ui/badge';
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from '@/components/ui/breadcrumb';
import { Button } from '@/components/ui/button';
import { SidebarInset, SidebarProvider, useSidebar } from '@/components/ui/sidebar';
import { Skeleton } from '@/components/ui/skeleton';
import { isTypingTarget, useHotkeys, type Binding } from '@/keyboard/hotkeys';
import { formatLocalTime } from '@/lib/format';
import { usePendingControls, useViewQuery } from '@/pending/pending';
import { AppSidebar } from './app-sidebar';
import { CommandPalette } from './command-palette';
import { recordRecentCompany } from './recent';
import { ShortcutSheet } from './shortcut-sheet';

const VIEW_TITLE: Record<string, string> = {
  today: 'Today',
  outbox: 'Outbox',
  linkedin: 'LinkedIn list',
  'needs-you': 'Needs you',
  'no-go': 'No-go list',
  progress: 'Progress',
  wins: 'Wins',
  done: 'Done',
};

const GO: [key: string, path: string, name: string][] = [
  ['t', '/today', 'Today'],
  ['o', '/outbox', 'Outbox'],
  ['l', '/linkedin', 'LinkedIn'],
  ['n', '/needs-you', 'Needs you'],
  ['x', '/no-go', 'No-go'],
  ['d', '/done', 'Done'],
  ['p', '/progress', 'Progress'],
  ['w', '/wins', 'Wins'],
];

/** The first path segment names the view: `/outbox/12?sel=12` is the `outbox` view. */
const viewOf = (pathname: string) => pathname.split('/')[1] ?? '';

function companyIdOf(param: string | undefined): number | null {
  const id = Number(param);
  return param !== undefined && Number.isInteger(id) && id > 0 ? id : null;
}

/**
 * The keys that work everywhere, the flush when the view changes, and the recent companies list.
 * None of the bindings set `inDialogs`: behind a dialog only its own keys and escape act.
 */
function ShellBehaviour({ openPalette, openSheet }: { openPalette: () => void; openSheet: () => void }) {
  const navigate = useNavigate();
  const location = useLocation();
  const { companyId } = useParams();
  const { toggleSidebar } = useSidebar();
  const { undoLast, flushPending } = usePendingControls();

  // Leaving a view commits what waits in its undo window. A new `?sel=` is the same view.
  const view = viewOf(location.pathname);
  const lastView = useRef(view);
  useEffect(() => {
    if (lastView.current === view) return;
    lastView.current = view;
    void flushPending();
  }, [view, flushPending]);

  const company = view === 'companies' ? companyIdOf(companyId) : null;
  useEffect(() => {
    if (company !== null) recordRecentCompany(company);
  }, [company]);

  // shadcn's SidebarProvider has its own Cmd/Ctrl+B listener on window, with no typing or dialog guard.
  // Stopping the event at the document leaves `mod+b` to the binding below (the hotkeys listener is on
  // the document too, so it still runs).
  useEffect(() => {
    const stop = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === 'b' && (e.metaKey || e.ctrlKey)) e.stopPropagation();
    };
    document.addEventListener('keydown', stop);
    return () => document.removeEventListener('keydown', stop);
  }, []);

  const group = 'Everywhere';
  const bindings: Binding[] = [
    ...GO.map(([key, path, name]): Binding => ({ keys: `g ${key}`, label: `Go to ${name}`, group, run: () => navigate(path) })),
    { keys: 'mod+k', label: 'Search and commands', group, run: openPalette },
    { keys: '/', label: 'Search', group, run: openPalette },
    { keys: '?', label: 'Keyboard shortcuts', group, run: openSheet },
    { keys: 'mod+b', label: 'Toggle sidebar', group, run: () => toggleSidebar() },
    { keys: 'z', label: 'Undo the last action', group, run: () => void undoLast() },
    {
      keys: 'escape',
      label: 'Leave the field, then go back',
      group,
      run: () => {
        const active = document.activeElement;
        if (active instanceof HTMLElement && isTypingTarget(active)) {
          active.blur();
          return;
        }
        // The first entry of this session has the key "default": there is nothing in the app to go back to.
        if (location.key !== 'default') navigate(-1);
      },
    },
  ];
  useHotkeys(bindings);
  return null;
}

function Crumbs() {
  const client = useClient();
  const { pathname } = useLocation();
  const { companyId } = useParams();
  const view = viewOf(pathname);
  const id = view === 'companies' ? companyIdOf(companyId) : null;
  const index = useViewQuery(queryKeys.search, () => client.searchIndex());
  const companyName = id === null ? undefined : index.data?.companies.find((c) => c.id === id)?.name;

  return (
    <Breadcrumb>
      <BreadcrumbList className="gap-1.5 text-sm sm:gap-1.5">
        {view === 'companies' ? (
          <>
            <BreadcrumbItem>Companies</BreadcrumbItem>
            <BreadcrumbSeparator />
            <BreadcrumbItem>
              <BreadcrumbPage className="font-medium">{companyName ?? 'Company'}</BreadcrumbPage>
            </BreadcrumbItem>
          </>
        ) : (
          <BreadcrumbItem>
            <BreadcrumbPage className="font-medium">{VIEW_TITLE[view] ?? 'Outreach'}</BreadcrumbPage>
          </BreadcrumbItem>
        )}
      </BreadcrumbList>
    </Breadcrumb>
  );
}

/** `onKeys` opens the shortcut sheet. */
function TopBar({ onKeys }: { onKeys?: () => void }) {
  const client = useClient();
  const shell = useViewQuery(queryKeys.shell, () => client.shell(), { pollMs: pollMs.default });
  return (
    <header className="flex h-12 flex-none items-center gap-2 border-b px-4">
      <Crumbs />
      <div className="ml-auto flex items-center gap-2">
        {client.source === 'sample' ? <Badge variant="warn">Sample data. Nothing is saved.</Badge> : null}
        {shell.data ? <span className="text-xs text-muted-foreground tabular-nums">Updated {formatLocalTime(shell.data.asOf)}</span> : null}
        <span aria-hidden className="h-4 w-px bg-border" />
        <Button
          variant="ghost"
          size="icon-sm"
          className="size-7"
          aria-label="Keyboard shortcuts"
          aria-haspopup="dialog"
          onClick={onKeys}
        >
          <Keyboard aria-hidden />
        </Button>
      </div>
    </header>
  );
}

function ViewSkeleton() {
  return (
    <div className="flex flex-col gap-3 px-6 pt-5" aria-hidden>
      <Skeleton className="h-7 w-56" />
      <Skeleton className="h-4 w-96" />
      <Skeleton className="mt-4 h-64 w-full max-w-5xl" />
    </div>
  );
}

/** The sidebar, the 48 px top bar and the current view (spec section 2). */
export function AppShell() {
  const [palette, setPalette] = useState(false);
  const [sheet, setSheet] = useState(false);
  return (
    <SidebarProvider style={{ '--sidebar-width': '240px' } as CSSProperties}>
      <ShellBehaviour openPalette={() => setPalette(true)} openSheet={() => setSheet(true)} />
      <AppSidebar onSearch={() => setPalette(true)} />
      <SidebarInset className="h-svh min-w-0 overflow-hidden">
        <TopBar onKeys={() => setSheet(true)} />
        <div className="min-h-0 flex-1 overflow-y-auto">
          <Suspense fallback={<ViewSkeleton />}>
            <Outlet />
          </Suspense>
        </div>
      </SidebarInset>
      <CommandPalette open={palette} onOpenChange={setPalette} onShowKeys={() => setSheet(true)} />
      <ShortcutSheet open={sheet} onOpenChange={setSheet} />
    </SidebarProvider>
  );
}
