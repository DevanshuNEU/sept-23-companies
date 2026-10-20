import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router';
import {
  Activity,
  Ban,
  Briefcase,
  Building2,
  CalendarCheck,
  CircleAlert,
  CircleCheck,
  Copy,
  Keyboard,
  Moon,
  Pause,
  Play,
  Send,
  Sun,
  User,
  type LucideIcon,
} from 'lucide-react';
import { toast } from 'sonner';
import {
  queryKeys,
  useClient,
  type Id,
  type OutboxView,
  type OutreachClient,
  type PostingHit,
  type SearchCompany,
  type SearchIndex,
  type SearchPerson,
  type SenderState,
  type ShellSummary,
} from '@/api';
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Kbd } from '@/components/ui/kbd';
import { useHotkeys } from '@/keyboard/hotkeys';
import { formatDueDay } from '@/lib/format';
import { usePendingWrite, useViewQuery, type Patch } from '@/pending/pending';
import { useTheme } from '@/theme/theme';
import { useRecentCompanies } from './recent';
import { keyCaps } from './shortcut-sheet';

/* ------------------------------------------------------------------ ranking */

// The old dashboard's shorthand for what people type into the search box.
const SYNONYMS: Record<string, string> = {
  mts: 'member of technical staff',
  fde: 'forward deployed',
  sf: 'san francisco',
  nyc: 'new york',
};

/** `mts engineer` becomes `member of technical staff engineer`. */
function expandQuery(q: string): string {
  return q
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .map((word) => SYNONYMS[word] ?? word)
    .join(' ');
}

/** What the query can mean: as typed, and with its synonyms spelled out. Empty for an empty query. */
function needlesOf(q: string): string[] {
  const typed = q.trim().toLowerCase();
  return typed === '' ? [] : [...new Set([typed, expandQuery(typed)])];
}

const MATCH_AT = { prefix: 0, rest: 1 } as const;

/**
 * Filters the search index in the browser on every keystroke. Companies match name, aliases and
 * domain; people match name, email and title. Order: a prefix on the name first, then companies in
 * progress, then recent ones (newest first), then the rest in index order. A person ranks by their
 * company's progress and recency.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function rankSearch(
  index: SearchIndex,
  q: string,
  recent: Id[],
): { companies: SearchCompany[]; people: SearchPerson[] } {
  const needles = needlesOf(q);
  const matches = (fields: (string | null)[]) =>
    needles.length === 0 ||
    fields.some((f) => f !== null && needles.some((n) => f.toLowerCase().includes(n)));
  const prefixes = (name: string) => needles.some((n) => name.toLowerCase().startsWith(n));
  const busy = new Set(index.companies.filter((c) => c.inProgress).map((c) => c.id));
  const recentAt = (id: Id) => {
    const at = recent.indexOf(id);
    return at === -1 ? recent.length : at;
  };

  function rank<T>(items: T[], name: (t: T) => string, companyId: (t: T) => Id): T[] {
    return items
      .map((item, i) => ({
        item,
        key: [
          prefixes(name(item)) ? MATCH_AT.prefix : MATCH_AT.rest,
          busy.has(companyId(item)) ? 0 : 1,
          recentAt(companyId(item)),
          i,
        ],
      }))
      .sort((a, b) => a.key.reduce((d, k, j) => d || k - (b.key[j] ?? 0), 0))
      .map((x) => x.item);
  }

  return {
    companies: rank(
      index.companies.filter((c) => matches([c.name, c.domain, ...c.aliases])),
      (c) => c.name,
      (c) => c.id,
    ),
    people: rank(
      index.people.filter((p) => matches([p.name, p.email, p.title])),
      (p) => p.name,
      (p) => p.companyId,
    ),
  };
}

/* ------------------------------------------------------------------ the palette */

const SHOWN = { companies: 8, people: 5, postings: 5 } as const;
const POSTING_DEBOUNCE_MS = 120;
const POSTING_MIN_CHARS = 3;

const VIEWS: { name: string; path: string; icon: LucideIcon; keys: string }[] = [
  { name: 'Today', path: '/today', icon: CalendarCheck, keys: 'g t' },
  { name: 'Outbox', path: '/outbox', icon: Send, keys: 'g o' },
  { name: 'LinkedIn list', path: '/linkedin', icon: Activity, keys: 'g l' },
  { name: 'Needs you', path: '/needs-you', icon: CircleAlert, keys: 'g n' },
  { name: 'No-go list', path: '/no-go', icon: Ban, keys: 'g x' },
  { name: 'Done companies', path: '/done', icon: CircleCheck, keys: 'g d' },
];

type Action = { id: string; label: string; sub: string; icon: LucideIcon; run: () => void };

const sub = 'text-xs font-normal text-muted-foreground';
const ERROR_LINE = 'px-3 py-2 text-[13px] text-bad';

/** Posting titles go to the API: as typed, and with synonyms spelled out when that differs. */
async function findPostings(client: OutreachClient, q: string): Promise<PostingHit[]> {
  if (q.length < POSTING_MIN_CHARS) return [];
  const lists = await Promise.all(needlesOf(q).map((needle) => client.searchPostings(needle)));
  const seen = new Set<Id>();
  return lists.flat().filter((p) => !seen.has(p.postingId) && seen.add(p.postingId));
}

/** What changes on screen the moment sending is paused or resumed, until the refetch lands. */
function sendingPatches(state: SenderState, pausedReason: string): Patch[] {
  return [
    { key: queryKeys.shell, apply: (d: ShellSummary) => ({ ...d, sending: { ...d.sending, state } }) },
    { key: queryKeys.outbox, apply: (d: OutboxView) => ({ ...d, sending: { ...d.sending, state, pausedReason } }) },
  ];
}

/** Changes nothing on screen, but names the queries the write refetches once it commits. */
const refetchSending: Patch[] = [
  { key: queryKeys.shell, apply: (d: ShellSummary) => d },
  { key: queryKeys.outbox, apply: (d: OutboxView) => d },
];

function PaletteKeys({ close }: { close: () => void }) {
  // The shell's own `mod+k` opens the palette and cannot run behind a dialog, so this one closes it.
  useHotkeys([{ keys: 'mod+k', label: 'Close search', group: 'Search', run: close, inDialogs: true }]);
  return null;
}

function PaletteBody({ close, showKeys }: { close: () => void; showKeys: () => void }) {
  const client = useClient();
  const navigate = useNavigate();
  const theme = useTheme();
  const write = usePendingWrite();
  const recent = useRecentCompanies();
  const [query, setQuery] = useState('');
  const [highlight, setHighlight] = useState('');
  const [debounced, setDebounced] = useState('');
  const q = query.trim();

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(q), POSTING_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [q]);

  const index = useViewQuery(queryKeys.search, () => client.searchIndex());
  const shell = useViewQuery(queryKeys.shell, () => client.shell());
  const today = useViewQuery(queryKeys.today, () => client.today());
  const postings = useViewQuery([...queryKeys.search, 'postings', debounced], () => findPostings(client, debounced));

  const ranked = useMemo(
    () => (index.data ? rankSearch(index.data, q, recent) : { companies: [], people: [] }),
    [index.data, q, recent],
  );
  const companies = ranked.companies.slice(0, SHOWN.companies);
  const people = ranked.people.slice(0, SHOWN.people);
  // The last posting results stay on screen until the next ones land, so rows below do not jump on each key.
  const [held, setHeld] = useState<PostingHit[]>([]);
  if (postings.data && postings.data !== held && debounced.length >= POSTING_MIN_CHARS) setHeld(postings.data);
  const hits = q.length >= POSTING_MIN_CHARS ? (postings.data ?? held).slice(0, SHOWN.postings) : [];
  const postingsFailed = q.length >= POSTING_MIN_CHARS && postings.isError;

  const needles = needlesOf(q);
  const wanted = (text: string) => needles.length === 0 || needles.some((n) => text.toLowerCase().includes(n));
  const views = VIEWS.filter((v) => wanted(`go to ${v.name}`));

  const sendingState = shell.data?.sending.state;
  const actions: Action[] = [];
  if (sendingState === 'paused_by_you') {
    actions.push({
      id: 'resume',
      label: 'Resume sending',
      sub: 'Starts the sender again',
      icon: Play,
      run: () =>
        write({
          id: 'sending',
          label: 'Sending resumed.',
          afterCommit: 'Sending resumed.',
          // No guess at the new state: it is "running" only inside the window on a weekday. The refetch shows it.
          patches: refetchSending,
          commit: (o) => client.resumeSending(false, o),
          immediate: true,
        }),
    });
  } else if (sendingState === 'running' || sendingState === 'outside_window' || sendingState === 'weekend') {
    // Where the sending strip offers Pause: it holds back what is approved for the next window too.
    actions.push({
      id: 'pause',
      label: 'Pause sending',
      sub: 'Stops the sender after the email in flight',
      icon: Pause,
      run: () =>
        write({
          id: 'sending',
          label: 'Sending paused. Nothing sends until you resume.',
          afterCommit: 'Sending paused.',
          patches: sendingPatches('paused_by_you', 'Paused by you'),
          commit: (o) => client.pauseSending(o),
          immediate: true,
        }),
    });
  }
  actions.push(
    {
      id: 'theme',
      label: 'Toggle theme',
      sub: 'Light or dark',
      icon: theme.resolved === 'dark' ? Sun : Moon,
      run: () => theme.toggle(),
    },
    { id: 'keys', label: 'Keyboard shortcuts', sub: 'Every key in one sheet', icon: Keyboard, run: showKeys },
    {
      id: 'copy',
      label: 'Copy picks for Claude',
      sub: "Today's picks as text",
      icon: Copy,
      run: () => void copyPicks(),
    },
  );
  const shownActions = actions.filter((a) => wanted(`${a.label} ${a.sub}`));

  async function copyPicks() {
    try {
      const view = today.data ?? (await client.today());
      const picked = [...view.proposed, ...view.backlog].filter((e) => e.state === 'picked');
      if (picked.length === 0) {
        toast('No picks yet. Nothing was copied.');
        return;
      }
      const lines = picked.map((e) => {
        const domain = e.company.domain ? ` (${e.company.domain})` : '';
        const role = e.role ? `${e.role.title}, ${e.role.location}` : 'no role named yet';
        return `- ${e.company.name}${domain}: ${role}`;
      });
      await navigator.clipboard.writeText(`Picks for ${formatDueDay(view.date)}, ${picked.length} of ${view.capacity}:\n${lines.join('\n')}`);
      toast('Copied');
    } catch {
      toast.error('Could not copy the picks. Nothing was changed. Try again.');
    }
  }

  // Where Enter goes for each row. The company page is the default; mod+Enter opens the Outbox filtered to it.
  const rows = new Map<string, { open: string; companyId: Id | null }>();
  const companyPath = (id: Id, tab?: string) => `/companies/${id}${tab ? `?tab=${tab}` : ''}`;
  for (const c of companies) rows.set(`company:${c.id}`, { open: companyPath(c.id), companyId: c.id });
  for (const p of people) rows.set(`person:${p.id}`, { open: companyPath(p.companyId, 'people'), companyId: p.companyId });
  for (const p of hits) rows.set(`posting:${p.postingId}`, { open: companyPath(p.companyId, 'roles'), companyId: p.companyId });
  for (const v of views) rows.set(`view:${v.path}`, { open: v.path, companyId: null });

  const go = (path: string) => {
    close();
    void navigate(path);
  };

  return (
    <Command
      shouldFilter={false}
      vimBindings={false}
      loop
      value={highlight}
      onValueChange={setHighlight}
      onKeyDown={(e) => {
        if (e.key !== 'Enter' || !(e.metaKey || e.ctrlKey)) return;
        e.preventDefault();
        const target = rows.get(highlight);
        if (target?.companyId != null) go(`/outbox?company=${target.companyId}`);
      }}
      className="**:data-[slot=command-input-wrapper]:h-12 [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-muted-foreground [&_[cmdk-group]]:px-2 [&_[cmdk-input-wrapper]_svg]:size-4"
    >
      <PaletteKeys close={close} />
      <CommandInput
        value={query}
        onValueChange={setQuery}
        className="h-12"
        placeholder="Search companies, people, views and actions"
        aria-label="Search"
      />
      <CommandList className="max-h-[380px]">
        {index.isError ? (
          <div role="alert" className={ERROR_LINE}>
            Search could not load. Nothing was changed. Try again.
          </div>
        ) : (
          <CommandEmpty className="py-6 text-center text-[13px] text-muted-foreground">
            Nothing matches. Try a company, a person or a view name.
          </CommandEmpty>
        )}
        {companies.length > 0 ? (
          <CommandGroup heading="Companies">
            {companies.map((c) => (
              <CommandItem key={c.id} value={`company:${c.id}`} onSelect={() => go(companyPath(c.id))}>
                <Building2 aria-hidden />
                <span>{c.name}</span>
                <span className={sub}>{c.statusLine}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        ) : null}
        {people.length > 0 ? (
          <CommandGroup heading="People">
            {people.map((p) => (
              <CommandItem key={p.id} value={`person:${p.id}`} onSelect={() => go(companyPath(p.companyId, 'people'))}>
                <User aria-hidden />
                <span>{p.name}</span>
                <span className={`${sub} min-w-0 truncate`}>
                  {p.title}, {p.companyName}
                </span>
                <span className={`${sub} ml-auto shrink-0`}>{p.statusLine}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        ) : null}
        {views.length > 0 ? (
          <CommandGroup heading="Go to">
            {views.map((v) => (
              <CommandItem key={v.path} value={`view:${v.path}`} onSelect={() => go(v.path)}>
                <v.icon aria-hidden />
                <span>{v.name}</span>
                <span className="ml-auto inline-flex gap-1">
                  {keyCaps(v.keys).map((cap, i) => (
                    <Kbd key={i}>{cap}</Kbd>
                  ))}
                </span>
              </CommandItem>
            ))}
          </CommandGroup>
        ) : null}
        {shownActions.length > 0 ? (
          <CommandGroup heading="Actions">
            {shownActions.map((a) => (
              <CommandItem
                key={a.id}
                value={`action:${a.id}`}
                onSelect={() => {
                  close();
                  a.run();
                }}
              >
                <a.icon aria-hidden />
                <span>{a.label}</span>
                <span className={sub}>{a.sub}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        ) : null}
        {hits.length > 0 ? (
          <CommandGroup heading="Postings">
            {hits.map((p) => (
              <CommandItem key={p.postingId} value={`posting:${p.postingId}`} onSelect={() => go(companyPath(p.companyId, 'roles'))}>
                <Briefcase aria-hidden />
                <span>{p.title}</span>
                <span className={sub}>{p.companyName}</span>
                {p.open ? null : <span className={`${sub} ml-auto`}>Closed</span>}
              </CommandItem>
            ))}
          </CommandGroup>
        ) : null}
        {postingsFailed ? (
          <div role="alert" className={ERROR_LINE}>
            Posting search could not load. Nothing was changed. Try again.
          </div>
        ) : null}
      </CommandList>
      <div className="flex gap-4 border-t px-3.5 py-2 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <Kbd>↑</Kbd>
          <Kbd>↓</Kbd> move
        </span>
        <span className="inline-flex items-center gap-1.5">
          <Kbd>Enter</Kbd> open
        </span>
        <span className="inline-flex items-center gap-1.5">
          {keyCaps('mod+enter').map((cap) => (
            <Kbd key={cap}>{cap}</Kbd>
          ))}{' '}
          in Outbox
        </span>
        <span className="inline-flex items-center gap-1.5">
          <Kbd>Esc</Kbd> close
        </span>
      </div>
    </Command>
  );
}

/**
 * Search and commands (spec section 2): companies, people, postings, views and actions in one box.
 * The body mounts only while open, so the query and the highlight start fresh each time.
 * Built from the shadcn dialog and command parts rather than `CommandDialog`, because that wrapper
 * cannot turn off cmdk's own filter, and the ranking here is ours.
 */
export function CommandPalette({
  open,
  onOpenChange,
  onShowKeys,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onShowKeys: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={false} className="top-[12vh] translate-y-0 gap-0 overflow-hidden p-0 sm:max-w-[560px]">
        <DialogHeader className="sr-only">
          <DialogTitle>Search and commands</DialogTitle>
          <DialogDescription>Search companies and people, go to a view, or run an action.</DialogDescription>
        </DialogHeader>
        <PaletteBody close={() => onOpenChange(false)} showKeys={onShowKeys} />
      </DialogContent>
    </Dialog>
  );
}
