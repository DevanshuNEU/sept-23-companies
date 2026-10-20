import type { ComponentType, ReactNode, SVGProps } from 'react';
import { NavLink } from 'react-router';
import { Activity, Ban, Building2, CalendarCheck, CircleAlert, CircleCheck, Moon, Search, Send, Sun } from 'lucide-react';
import { pollMs, queryKeys, useClient, type JobRunSummary, type SenderState, type ShellSummary } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Kbd } from '@/components/ui/kbd';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from '@/components/ui/sidebar';
import { StatusBadge } from '@/components/status-badge';
import { formatDayTime, formatMonth, formatWeekdayTime } from '@/lib/format';
import { jobKindLabel, jobStatus, type Tone } from '@/lib/status';
import { cn } from '@/lib/utils';
import { useViewQuery } from '@/pending/pending';
import { useTheme } from '@/theme/theme';
import { useRecentCompanies } from './recent';

const ITEM =
  'h-8 gap-2 px-2 text-sm aria-[current=page]:bg-sidebar-accent aria-[current=page]:font-medium [&>svg]:text-muted-foreground aria-[current=page]:[&>svg]:text-foreground';
const COUNT = 'ml-auto text-xs text-muted-foreground tabular-nums';
const DOT: Record<Tone, string> = {
  ok: 'bg-ok-dot',
  info: 'bg-info-dot',
  warn: 'bg-warn-dot',
  bad: 'bg-bad-dot',
  idle: 'bg-idle-dot',
};

// The footer's words for the sender. The Outbox strip says Running; the footer names the activity.
const SENDER: Record<SenderState, { label: string; tone: Tone }> = {
  running: { label: 'Sending', tone: 'ok' },
  paused_by_you: { label: 'Paused by you', tone: 'idle' },
  paused_hard_block: { label: 'Paused: hard block', tone: 'bad' },
  outside_window: { label: 'Outside the window', tone: 'idle' },
  weekend: { label: 'Weekend', tone: 'idle' },
  signed_out: { label: 'Gmail signed out', tone: 'bad' },
};

/** Lucide dropped brand icons; this is its old `linkedin` glyph, as the mockups draw it. */
function LinkedInIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width={24} height={24} fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-2-2 2 2 0 0 0-2 2v7h-4v-7a6 6 0 0 1 6-6z" />
      <rect x="2" y="9" width="4" height="12" />
      <circle cx="4" cy="4" r="2" />
    </svg>
  );
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

function NavItem({
  to,
  icon: Icon,
  label,
  described,
  children,
}: {
  to: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  label: string;
  /** What the counts mean, for the link's accessible name. */
  described?: string;
  children?: ReactNode;
}) {
  return (
    <SidebarMenuItem>
      <SidebarMenuButton asChild className={ITEM}>
        <NavLink to={to} aria-label={described ? `${label}, ${described}` : undefined}>
          <Icon aria-hidden />
          <span>{label}</span>
          {children}
        </NavLink>
      </SidebarMenuButton>
    </SidebarMenuItem>
  );
}

function Count({ value, after = false }: { value: number | string; after?: boolean }) {
  return (
    <span aria-hidden className={cn(COUNT, after && 'ml-1.5')}>
      {value}
    </span>
  );
}

function CountBadge({ value, tone, dot = false }: { value: number; tone: 'ok' | 'warn'; dot?: boolean }) {
  return (
    <Badge aria-hidden variant={tone} className="ml-auto h-5 px-1.5 tabular-nums">
      {dot ? <span aria-hidden className={cn('size-1.5 rounded-full', DOT[tone])} /> : null}
      {value}
    </Badge>
  );
}

function ViewItems({ s }: { s: ShellSummary | undefined }) {
  const outboxParts = s
    ? [
        s.unhandledReplies > 0 ? plural(s.unhandledReplies, 'reply to handle', 'replies to handle') : '',
        s.draftsToReview > 0 ? plural(s.draftsToReview, 'draft to review', 'drafts to review') : '',
      ].filter(Boolean)
    : [];
  return (
    <SidebarGroup className="px-0 pt-2 pb-1">
      <nav aria-label="Views">
        <SidebarMenu className="gap-0.5">
          <NavItem to="/today" icon={CalendarCheck} label="Today" described={s && `${s.pickedToday} of ${s.capacityToday} picked`}>
            {s ? <Count value={`${s.pickedToday} of ${s.capacityToday}`} /> : null}
          </NavItem>
          <NavItem to="/outbox" icon={Send} label="Outbox" described={outboxParts.join(', ') || undefined}>
            {s && s.unhandledReplies > 0 ? <CountBadge value={s.unhandledReplies} tone="ok" dot /> : null}
            {s && s.draftsToReview > 0 ? <Count value={s.draftsToReview} after={s.unhandledReplies > 0} /> : null}
          </NavItem>
          <NavItem to="/linkedin" icon={LinkedInIcon} label="LinkedIn" described={s && s.linkedinToReach > 0 ? `${s.linkedinToReach} to reach` : undefined}>
            {s && s.linkedinToReach > 0 ? <Count value={s.linkedinToReach} /> : null}
          </NavItem>
          <NavItem to="/needs-you" icon={CircleAlert} label="Needs you" described={s && s.openReviewItems > 0 ? `${s.openReviewItems} open` : undefined}>
            {s && s.openReviewItems > 0 ? <CountBadge value={s.openReviewItems} tone="warn" /> : null}
          </NavItem>
        </SidebarMenu>
      </nav>
    </SidebarGroup>
  );
}

function ListItems({ s }: { s: ShellSummary | undefined }) {
  return (
    <SidebarGroup className="px-0 pt-2 pb-1">
      <nav aria-label="Lists">
        <SidebarGroupLabel className="text-muted-foreground">Lists</SidebarGroupLabel>
        <SidebarMenu className="gap-0.5">
          <NavItem to="/no-go" icon={Ban} label="No-go" described={s && s.noGoCount > 0 ? `${s.noGoCount}` : undefined}>
            {s && s.noGoCount > 0 ? <Count value={s.noGoCount} /> : null}
          </NavItem>
          <NavItem to="/done" icon={CircleCheck} label="Done" described={s && s.doneCount > 0 ? `${s.doneCount}` : undefined}>
            {s && s.doneCount > 0 ? <Count value={s.doneCount} /> : null}
          </NavItem>
        </SidebarMenu>
      </nav>
    </SidebarGroup>
  );
}

function RecentItems() {
  const client = useClient();
  const ids = useRecentCompanies();
  const index = useViewQuery(queryKeys.search, () => client.searchIndex());
  const byId = new Map((index.data?.companies ?? []).map((c) => [c.id, c.name]));
  const items = ids.flatMap((id) => {
    const name = byId.get(id);
    return name === undefined ? [] : [{ id, name }];
  });
  if (items.length === 0) return null;
  return (
    <SidebarGroup className="px-0 pt-2 pb-1">
      <nav aria-label="Recent companies">
        <SidebarGroupLabel className="text-muted-foreground">Recent</SidebarGroupLabel>
        <SidebarMenu className="gap-0.5">
          {items.map(({ id, name }) => (
            <SidebarMenuItem key={id}>
              <SidebarMenuButton asChild className={cn(ITEM, 'h-7 text-[13px] text-muted-foreground aria-[current=page]:text-foreground')}>
                <NavLink to={`/companies/${id}`}>
                  <Building2 aria-hidden />
                  <span>{name}</span>
                </NavLink>
              </SidebarMenuButton>
            </SidebarMenuItem>
          ))}
        </SidebarMenu>
      </nav>
    </SidebarGroup>
  );
}

function JobRun({ job }: { job: JobRunSummary }) {
  const label = jobKindLabel[job.job];
  const counts = job.counts.map((c) => `${c.label} ${c.value.toLocaleString('en-US')}`).join(', ');
  return (
    <li aria-label={label} className="grid gap-1 py-2.5 first:pt-0 last:pb-0">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium">{label}</span>
        <span className="ml-auto flex gap-1">
          {job.late ? <StatusBadge status={jobStatus.late} /> : null}
          <StatusBadge status={jobStatus[job.status]} />
        </span>
      </div>
      <p className="text-xs text-muted-foreground tabular-nums">{formatDayTime(job.startedAt)}</p>
      {counts ? <p className="text-xs text-muted-foreground">{counts}</p> : null}
      {job.error ? <p className="text-xs text-bad">{job.error}</p> : null}
      {job.late && job.expectedNextAt ? (
        <p className="text-xs text-warn">Expected {formatDayTime(job.expectedNextAt)}</p>
      ) : null}
    </li>
  );
}

/** "Boards read Mon 08:04", which opens the last run of every job. */
function BoardsRead({ jobs }: { jobs: JobRunSummary[] }) {
  const crawl = jobs.find((j) => j.job === 'crawl');
  const tone: Tone = !crawl ? 'idle' : crawl.status === 'failed' ? 'bad' : crawl.late ? 'warn' : 'idle';
  const text = crawl ? `Boards read ${formatWeekdayTime(crawl.startedAt)}` : 'Boards not read yet';
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={cn(
            'flex h-5 items-center gap-2 rounded-md text-left text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring',
            tone === 'warn' && 'text-warn',
            tone === 'bad' && 'text-bad',
          )}
        >
          <Activity aria-hidden className="size-3.5" />
          {text}
        </button>
      </PopoverTrigger>
      <PopoverContent side="top" align="start" className="w-80">
        <p className="mb-3 text-sm font-semibold">Last runs</p>
        <ul className="divide-y">
          {jobs.map((job) => (
            <JobRun key={job.job} job={job} />
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  );
}

function Footer({ s }: { s: ShellSummary | undefined }) {
  if (!s) return null;
  const sender = SENDER[s.sending.state];
  return (
    <SidebarFooter className="gap-2.5 border-t border-sidebar-border p-4 text-xs text-muted-foreground">
      <div className="flex h-5 items-center gap-2">
        <span data-testid="sender-dot" aria-hidden className={cn('size-2 rounded-full', DOT[sender.tone])} />
        <span className="font-medium text-foreground">{sender.label}</span>
        <span className="ml-auto whitespace-nowrap tabular-nums">{`${s.sending.sentToday} of ${s.sending.limitToday} today`}</span>
      </div>
      <div className="flex h-5 items-center gap-2">
        <span aria-hidden className={cn('size-2 rounded-full', DOT[s.repliesThisMonth > 0 ? 'ok' : 'idle'])} />
        <span className="font-medium text-foreground">{plural(s.repliesThisMonth, 'reply', 'replies')}</span>
        <span className="ml-auto">{`in ${formatMonth(s.asOf)}`}</span>
      </div>
      <BoardsRead jobs={s.jobs} />
    </SidebarFooter>
  );
}

/**
 * The left sidebar (spec section 2): the views with their counts, the lists, recent companies, and a
 * footer with the sender, replies this month and the last board read. Every count comes from the shell
 * summary; the browser computes none of them. `onSearch` opens the command palette (Task 13 mounts it).
 */
export function AppSidebar({ onSearch }: { onSearch?: () => void }) {
  const client = useClient();
  const { resolved, toggle } = useTheme();
  const shell = useViewQuery(queryKeys.shell, () => client.shell(), { pollMs: pollMs.default });
  const s = shell.data;

  return (
    <Sidebar>
      <SidebarHeader className="gap-1 px-2 pt-3 pb-0">
        <div className="flex h-8 items-center justify-between pr-1 pl-2">
          <span className="flex items-center gap-2 font-semibold">
            <span aria-hidden className="grid size-6 place-items-center rounded-md bg-primary text-xs font-semibold text-primary-foreground">
              O
            </span>
            Outreach
          </span>
          <Button variant="ghost" size="icon-sm" className="size-7" aria-label="Switch theme" onClick={toggle}>
            {resolved === 'dark' ? <Sun aria-hidden /> : <Moon aria-hidden />}
          </Button>
        </div>
        <button
          type="button"
          onClick={onSearch}
          aria-haspopup="dialog"
          className="mt-2 mb-1 flex h-8 items-center gap-2 rounded-md border border-input bg-background px-2 text-left text-[13px] text-muted-foreground shadow-xs outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring"
        >
          <Search aria-hidden className="size-4" />
          Search
          <Kbd className="ml-auto">⌘K</Kbd>
        </button>
      </SidebarHeader>
      <SidebarContent className="gap-0 px-2">
        <ViewItems s={s} />
        <ListItems s={s} />
        <RecentItems />
      </SidebarContent>
      <Footer s={s} />
    </Sidebar>
  );
}
