import { useRef, useState, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import { Check as CheckIcon, Undo2 } from 'lucide-react';
import { toast } from 'sonner';
import {
  ApiError,
  newRequestId,
  pollMs,
  queryKeys,
  useClient,
  type Check,
  type CheckFix,
  type Id,
  type OutboxDetail,
  type OutboxItem,
  type OutboxView,
  type Revision,
  type RoleKind,
  type ShellSummary,
} from '@/api';
import { CheckList } from '@/components/check-list';
import { DraftEditor } from '@/components/draft-editor';
import { FollowupTrack } from '@/components/followup-track';
import { QueryState, StateBanner } from '@/components/state-views';
import { StatusBadge } from '@/components/status-badge';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Kbd } from '@/components/ui/kbd';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useHotkeys } from '@/keyboard/hotkeys';
import { ageDays, formatAge, formatDueDay } from '@/lib/format';
import { useEdits, type Edit } from '@/pending/edits';
import { usePendingControls, usePendingWrite, useViewQuery, type Patch } from '@/pending/pending';
import { newestRevision, noteRevision, withRevision } from '@/pending/revisions';
import { safeHref } from '@/lib/safe-href';
import { CompareDialog } from './compare-dialog';
import { heldLine, useCompanyActions } from './company-actions';
import { rowStatus } from './outbox-list';
import { fewerDrafts, patchDetailItem, patchRow } from './outbox-patches';
import { ReplyDetail, Thread, type ReplyChoice } from './reply-detail';

const SAVE_FIRST = 'Save your edits first (Cmd S)';

const ROLE_WORD: Record<RoleKind, string> = {
  recruiter: 'recruiter',
  engineer: 'engineer',
  manager: 'manager',
  founder: 'founder',
  alum: 'alum',
  other: 'other',
};
const ROLE_BADGE: Record<RoleKind, string> = {
  recruiter: 'Recruiter',
  engineer: 'Engineer',
  manager: 'Manager',
  founder: 'Founder',
  alum: 'Alum',
  other: 'Other',
};

type Kind = 'reply' | 'draft' | 'approved' | 'sent' | 'locked' | 'closed';

function kindOf(i: OutboxItem): Kind {
  if (i.touchState === 'replied') return 'reply';
  if (i.touchState === 'closed' || i.touchState === 'bounced') return 'closed';
  switch (i.state) {
    case 'drafted':
      return 'draft';
    case 'approved':
      return 'approved';
    case 'sent':
      return 'sent';
    case 'sending':
    case 'send_unknown':
      return 'locked';
    default:
      return 'closed';
  }
}

const FIX_HINT: Record<string, string> = {
  compare: 'Edit, then approve.',
  review_item: 'Fix it in Needs you, then approve.',
  no_go: 'Open the no-go entry first.',
  none: 'Fix it, then approve.',
};

/** "Blocked: near-identical to Ava Moreno's draft. Edit, then approve." The words come from the check, never from a guess. */
function blockText(blocks: Check[]): string {
  const first = blocks[0];
  if (!first) return '';
  const title = first.title.replace(/\s*\([^)]*\)\s*$/, '');
  const name = /^[A-Z][a-z]/.test(title) ? `${title.charAt(0).toLowerCase()}${title.slice(1)}` : title;
  const more = blocks.length > 1 ? ` (and ${blocks.length - 1} more)` : '';
  return `Blocked: ${name}${more}. ${FIX_HINT[first.fix?.kind ?? 'none'] ?? FIX_HINT.none}`;
}

const initials = (name: string) =>
  name
    .split(/\s+/)
    .map((w) => w.charAt(0))
    .slice(0, 2)
    .join('')
    .toUpperCase();

function DetailHeader({ detail }: { detail: OutboxDetail }) {
  const { item } = detail;
  const { person, company } = item;
  const status = rowStatus(item);
  // The badge repeats what the kind badge or the plain Draft state already says.
  const showStatus = status.label !== 'Draft' && status.label !== `Follow-up ${item.seq} of 3`;
  return (
    <header className="flex flex-none items-start gap-3 border-b px-5 py-4">
      <Avatar size="lg">
        <AvatarFallback>{initials(person.name)}</AvatarFallback>
      </Avatar>
      <div className="min-w-0 grow">
        <h2 className="text-base font-semibold">{person.name}</h2>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[13px] text-muted-foreground">
          <span>
            {`${person.title} at `}
            <Link to={`/companies/${company.id}`} className="underline decoration-border underline-offset-3 hover:decoration-current">
              {company.name}
            </Link>
          </span>
          {person.email ? <span className="font-mono text-xs">{person.email}</span> : null}
          {safeHref(person.linkedinUrl) ? (
            <a href={safeHref(person.linkedinUrl)} target="_blank" rel="noreferrer" className="underline decoration-border underline-offset-3 hover:decoration-current">
              LinkedIn
            </a>
          ) : null}
        </div>
      </div>
      <div className="flex flex-none flex-wrap justify-end gap-1.5">
        <Badge variant={person.roleKind === 'recruiter' ? 'warn' : 'outline'}>{ROLE_BADGE[person.roleKind]}</Badge>
        {item.touchState !== 'replied' ? <Badge variant="outline">{item.seq === 0 ? 'First email' : `Follow-up ${item.seq} of 3`}</Badge> : null}
        {showStatus ? <StatusBadge status={status} dot /> : null}
      </div>
    </header>
  );
}

function Cell({ k, v, s }: { k: string; v: string; s?: string }) {
  return (
    <div className="grid min-w-0 flex-1 gap-0.5 rounded-md border px-3 py-2 text-[13px]">
      <span className="text-xs text-muted-foreground">{k}</span>
      <span className="font-medium">{v}</span>
      {s ? <span className="text-xs text-muted-foreground">{s}</span> : null}
    </div>
  );
}

/** Spec 5.3, "Context row": the role to name, the hook, and when this email goes. All of it comes from the API. */
function ContextRow({ detail }: { detail: OutboxDetail }) {
  const { item, role, hook, asOf } = detail;
  const { schedule } = item;
  // A calendar date read at 17:00 UTC is noon or 1 pm in Boston: the same calendar day there.
  const hookAge = hook.on ? ageDays(`${hook.on}T17:00:00Z`, asOf) : null;
  const hookWhen = hook.on ? `${formatDueDay(hook.on).replace(/^\S+ /, '')}, ${hookAge === 1 ? '1 day' : `${hookAge} days`} old` : undefined;
  const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
  return (
    <div className="flex flex-wrap gap-2">
      <Cell k="Role to name" v={role ? role.title : 'No open role on file'} s={role ? `Posted ${formatAge(role.firstSeenAt, asOf)}, ${role.location}` : undefined} />
      <Cell k="Hook" v={hook.text} s={hookWhen} />
      {schedule ? (
        schedule.reason === 'recruiter_day_2' ? (
          <Cell k="If approved now" v={schedule.text} s={`Day 2 for ${item.company.name}, after engineers`} />
        ) : (
          <Cell
            k={`${item.company.name} today`}
            v={`${schedule.companySentToday} sent, ${plural(schedule.companySlotsLeft, 'slot', 'slots')} left`}
            s={schedule.text}
          />
        )
      ) : null}
    </div>
  );
}

function ReadOnlyDraft({ subject, body }: { subject: string; body: string }) {
  return (
    <div className="overflow-hidden rounded-md border">
      <div className="flex items-baseline gap-2 border-b px-3 py-2 text-sm">
        <span className="w-[52px] text-xs text-muted-foreground">Subject</span>
        <b className="font-medium">{subject}</b>
      </div>
      <p className="p-3 text-sm leading-[22px] break-words whitespace-pre-wrap">{body}</p>
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div aria-hidden className="grid gap-4 p-5">
      <span className="h-10 w-64 animate-pulse rounded bg-muted" />
      <span className="h-16 w-full animate-pulse rounded bg-muted" />
      <span className="h-64 w-full animate-pulse rounded bg-muted" />
    </div>
  );
}

/** The state line of an email that cannot change: why it is where it is. */
function StateLine({ detail }: { detail: OutboxDetail }) {
  const { item, bounceReason } = detail;
  if (item.touchState === 'bounced' && bounceReason) {
    return (
      <StateBanner tone="bad" title="This email bounced">
        <span className="font-mono text-xs break-words">{bounceReason}</span>
      </StateBanner>
    );
  }
  return <p className="text-sm text-muted-foreground">{item.summary}</p>;
}

/** The email he is looking at, and every action on it. Only here is `client.approve` called, and only from his click or key. */
function DetailBody({
  detail,
  items,
}: {
  detail: OutboxDetail;
  items: OutboxItem[];
}) {
  const client = useClient();
  // Unsaved text per email, so J, K, another view and back never throw it away.
  const edits = useEdits();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const write = usePendingWrite();
  const { flushPending, cancel } = usePendingControls();
  const articleRef = useRef<HTMLElement>(null);
  const saving = useRef(false);

  const { item } = detail;
  const { person, company, touchId, messageId } = item;
  const kind = kindOf(item);
  const detailKey = queryKeys.outboxDetail(touchId);
  /** Every revision of this email the server hands back is noted, so a write sent as the tab closes can use it. */
  const revisionKey = `message:${messageId}`;
  const noted = (d: OutboxDetail): OutboxDetail => {
    noteRevision(queryClient, revisionKey, d.item.revision);
    return d;
  };

  const [edit, setEditState] = useState<Edit | null>(() => edits.get(messageId) ?? null);
  const [serverConflict, setServerConflict] = useState(false);
  const [editing, setEditing] = useState(false);
  const [comparing, setComparing] = useState<Id | null>(null);

  const setEdit = (next: Edit | null, saved: OutboxDetail = detail) => {
    // Typing back to the saved text leaves nothing unsaved, so it is not kept (and the page stops asking before it closes).
    const same = next !== null && next.subject === saved.item.subject && next.body === saved.body;
    if (next && !same) edits.set(messageId, next);
    else edits.delete(messageId);
    setEditState(same ? null : next);
  };

  const subject = edit?.subject ?? item.subject;
  const body = edit?.body ?? detail.body;
  const dirty = edit !== null && (edit.subject !== item.subject || edit.body !== detail.body);
  // A base ahead of the cached revision is a save that just landed and a cache still catching up, not a conflict.
  const conflict = dirty && (serverConflict || edit.base < item.revision);
  const blocks = detail.checks.filter((c) => c.severity === 'block');
  // Approve is refused, not hidden: unsaved edits make every check stale, a block is a hard no.
  const gateText = dirty ? SAVE_FIRST : blocks.length > 0 ? blockText(blocks) : '';

  const first = person.name.split(' ')[0] ?? person.name;
  const asOf = detail.asOf;
  const willResume =
    kind === 'reply'
      ? detail.held.map((h) => heldLine(h, asOf))
      : items
          .filter((i) => i.company.id === company.id && i.group === 'paused')
          .map((i) => heldLine({ personName: i.person.name, state: i.state, seq: i.seq, dueOn: i.nextDue?.on ?? null }, asOf));
  const companyActions = useCompanyActions({ company, touchId, items, willResume });
  const companyLive = company.status !== 'closed' && company.status !== 'removed';
  const hasCompanyActions = companyLive && (kind === 'draft' || kind === 'approved' || kind === 'sent' || kind === 'reply');

  const refreshList = () => void queryClient.invalidateQueries({ queryKey: queryKeys.outbox, exact: true });

  /* ------------------------------------------------------------ save */

  async function save(base?: Revision): Promise<OutboxDetail | null> {
    if (!edit || !dirty || saving.current) return null;
    saving.current = true;
    // The map is written the moment he types or a save rebases it, so it is never behind this render.
    const sent = edits.get(messageId) ?? edit;
    try {
      const saved = await client.saveMessage(messageId, { subject: sent.subject, body: sent.body, revision: base ?? sent.base }, { requestId: newRequestId() });
      queryClient.setQueryData(detailKey, noted(saved));
      // He may have typed while the save was in flight: clear the edit only if it is still what was sent,
      // else keep it and start it from the revision this save made.
      const now = edits.get(messageId);
      if (now && (now.subject !== sent.subject || now.body !== sent.body)) setEdit({ ...now, base: saved.item.revision }, saved);
      else setEdit(null);
      setServerConflict(false);
      refreshList();
      toast('Saved. Checks run again.', { id: 'saved' });
      return saved;
    } catch (err) {
      if (err instanceof ApiError && err.code === 'conflict') setServerConflict(true);
      else toast.error(err instanceof ApiError ? err.message : 'The save did not go through. Your text is still here. Try again.');
      return null;
    } finally {
      saving.current = false;
    }
  }

  /** The newer version from the server, put in the cache. Null when it cannot be read. */
  async function loadFresh(): Promise<OutboxDetail | null> {
    try {
      const fresh = noted(await client.outboxDetail(touchId));
      queryClient.setQueryData(detailKey, fresh);
      refreshList();
      return fresh;
    } catch {
      toast.error('Could not load the newer version. Nothing was changed. Try again.');
      return null;
    }
  }

  const loadNewer = async () => {
    if (!(await loadFresh())) return;
    setEdit(null);
    setServerConflict(false);
  };

  const saveMine = async () => {
    const fresh = await loadFresh();
    if (fresh) await save(fresh.item.revision);
  };

  /* ------------------------------------------------------------ approve, skip, withdraw */

  function approveFrom(d: OutboxDetail) {
    const it = d.item;
    if (it.state !== 'drafted' || it.touchState !== 'open') return;
    const list: Patch<OutboxView> = { key: queryKeys.outbox, apply: (v) => patchRow(v, it.touchId, (i) => ({ ...i, state: 'approved', group: 'approved' })) };
    const det: Patch<OutboxDetail> = { key: detailKey, apply: (x) => patchDetailItem(x, { state: 'approved', group: 'approved' }) };
    const shell: Patch<ShellSummary> = { key: queryKeys.shell, apply: (s) => fewerDrafts(s) };
    write({
      id: `approve:${it.messageId}`,
      label: it.schedule ? `Approved ${it.person.name}. ${it.schedule.text}.` : `Approved ${it.person.name}.`,
      afterCommit: 'Approved. Press U to withdraw.',
      patches: [list, det, shell],
      // The revision he read: if Claude saved a newer version since, the API refuses and says so.
      commit: async (o) => {
        const done = noted(await client.approve(it.messageId, it.revision, o));
        edits.delete(it.messageId); // what was approved is the saved text; nothing typed after it can go out
        return done;
      },
    });
  }

  /** The one way to approve from the screen: a click on Approve, the A key, or mod+enter. */
  const tryApprove = () => {
    if (item.state !== 'drafted' || item.touchState !== 'open') return;
    if (gateText) {
      toast(gateText, { id: 'approve-refused' });
      return;
    }
    approveFrom(detail);
  };

  const saveThenApprove = async () => {
    if (kind !== 'draft') return;
    let d = detail;
    if (dirty) {
      const saved = await save();
      if (!saved) return;
      d = saved;
    }
    const stillBlocked = d.checks.filter((c) => c.severity === 'block');
    if (stillBlocked.length > 0) {
      toast(blockText(stillBlocked), { id: 'approve-refused' });
      return;
    }
    approveFrom(d);
  };

  const skip = () => {
    if (kind !== 'draft' && kind !== 'approved') return;
    // An approval still inside its undo window never reached the API: drop it, so the skip is the only write
    // and a skip that fails leaves a draft, never an approved email the sender would pick up.
    const approveDropped = cancel(`approve:${messageId}`);
    const wasDraft = approveDropped || item.state === 'drafted';
    const skipped = { state: 'skipped', touchState: 'closed', group: 'closed', schedule: null, nextDue: null, summary: 'Skipped by you' } as const;
    const list: Patch<OutboxView> = { key: queryKeys.outbox, apply: (v) => patchRow(v, touchId, (i) => ({ ...i, ...skipped })) };
    const det: Patch<OutboxDetail> = { key: detailKey, apply: (x) => patchDetailItem(x, skipped) };
    const shell: Patch<ShellSummary> = { key: queryKeys.shell, apply: (s) => (wasDraft ? fewerDrafts(s) : s) };
    const onScreen = item.revision;
    write({
      id: `skip:${messageId}`,
      label: item.seq > 0 ? `Skipped follow-up ${item.seq} for ${person.name}. Nothing goes out.` : `Skipped ${person.name}. No email goes out.`,
      afterCommit: `Skipped ${person.name}.`,
      patches: [list, det, shell],
      // An approval may land first and move the revision, so the skip reads it when it commits. As the tab
      // closes it cannot read: it sends with the newest revision known (on screen, cached, or from a commit).
      commit: async (o) => {
        const known = () => newestRevision(queryClient, revisionKey, onScreen, queryClient.getQueryData<OutboxDetail>(detailKey)?.item.revision);
        const read = async () => noted(await client.outboxDetail(touchId)).item.revision;
        const done = await withRevision(o, known, read, (revision) => client.skip(messageId, revision, o));
        edits.delete(messageId); // a skipped email has no editor, so its unsaved text would linger
        return done;
      },
    });
  };

  const withdraw = async () => {
    if (kind !== 'approved') return;
    // An approval still inside its undo window never reached the API: dropping it is the same as Undo.
    if (cancel(`approve:${messageId}`)) return;
    // An approval whose commit already started: let it land, then withdraw it.
    if (queryClient.getQueryData<OutboxDetail>(detailKey)?.item.state === 'drafted') await flushPending();
    const revision = queryClient.getQueryData<OutboxDetail>(detailKey)?.item.revision ?? item.revision;
    const back = { state: 'drafted', group: 'review' } as const;
    const list: Patch<OutboxView> = { key: queryKeys.outbox, apply: (v) => patchRow(v, touchId, (i) => ({ ...i, ...back })) };
    const det: Patch<OutboxDetail> = { key: detailKey, apply: (x) => patchDetailItem(x, back) };
    const shell: Patch<ShellSummary> = { key: queryKeys.shell, apply: (s) => ({ ...s, draftsToReview: s.draftsToReview + 1 }) };
    write({
      id: `withdraw:${messageId}`,
      label: `Approval withdrawn for ${person.name}. It will not send.`,
      afterCommit: `Approval withdrawn for ${person.name}.`,
      immediate: true,
      patches: [list, det, shell],
      commit: async (o) => noted(await client.unapprove(messageId, revision, o)),
    });
  };

  /* ------------------------------------------------------------ role kind and reply kind */

  const setRoleKind = (next: RoleKind) => {
    if (next === person.roleKind) return;
    const list: Patch<OutboxView> = {
      key: queryKeys.outbox,
      apply: (v) => ({ ...v, items: v.items.map((i) => (i.person.id === person.id ? { ...i, person: { ...i.person, roleKind: next } } : i)) }),
    };
    const det: Patch<OutboxDetail> = { key: detailKey, apply: (x) => patchDetailItem(x, { person: { ...x.item.person, roleKind: next } }) };
    // The checks run again on the API, so this saves at once rather than waiting out an undo window.
    write({
      id: `role:${person.id}`,
      label: `Marked ${person.name} as ${ROLE_WORD[next]}. Checks run again.`,
      afterCommit: `Marked ${person.name} as ${ROLE_WORD[next]}.`,
      immediate: true,
      patches: [list, det],
      commit: (o) => client.setRoleKind(person.id, next, person.revision, o),
    });
  };

  const onReplyKind = (next: ReplyChoice) => {
    if (!detail.reply || detail.reply.kind === next) return;
    const release = next === 'auto_reply';
    const det: Patch<OutboxDetail> = {
      key: detailKey,
      apply: (x) => ({
        ...x,
        reply: x.reply ? { ...x.reply, kind: next } : x.reply,
        item: release ? { ...x.item, company: { ...x.item.company, heldByTouchId: null } } : x.item,
      }),
    };
    const word: Record<ReplyChoice, string> = {
      interested: 'interested',
      not_now: 'not now',
      said_no: 'said no',
      asked_to_stop: 'asked to stop',
      auto_reply: 'an auto-reply',
    };
    // Said no and Asked to stop add a no-go row and feed the clean-day rule, so they commit at once (lead decision 2).
    const final = next === 'said_no' || next === 'asked_to_stop';
    // The last choice wins: a choice still inside its window is dropped, or the store would ignore this one.
    cancel(`reply:${touchId}`);
    write({
      id: `reply:${touchId}`,
      label: final
        ? `${person.name} ${word[next]}. Added to the no-go list.`
        : release
          ? `Marked ${person.name}'s reply as an auto-reply. The hold is released.`
          : `Marked ${person.name}'s reply as ${word[next]}.`,
      afterCommit: `Marked ${person.name}'s reply as ${word[next]}.`,
      immediate: final,
      patches: [det],
      commit: (o) => client.setReplyKind(touchId, next, detail.touchRevision, o),
    });
  };

  /* ------------------------------------------------------------ keys */

  const focusEditor = () => articleRef.current?.querySelector<HTMLElement>('textarea')?.focus();
  const leaveEditor = () => {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    document.querySelector<HTMLElement>(`[role="option"][data-touch="${touchId}"]`)?.focus({ preventScroll: true });
  };
  const togglePause = () => (companyActions.held ? companyActions.askResume() : companyActions.pause());

  // Every key but escape acts on this email, so one that follows j, k or a click waits for that move to render.
  useHotkeys(
    [
      { keys: 'a', label: 'Approve the selected draft', group: 'Outbox', enabled: kind === 'draft', run: tryApprove },
      { keys: 'e', label: 'Edit the body', group: 'Outbox', enabled: kind === 'draft', run: focusEditor },
      // Always taken, so the browser's own save dialog never opens on this view.
      { keys: 'mod+s', label: 'Save edits', group: 'Outbox', run: () => void save() },
      { keys: 'mod+enter', label: 'Save and approve', group: 'Outbox', enabled: kind === 'draft', run: () => void saveThenApprove() },
      { keys: 's', label: 'Skip (undo with Z)', group: 'Outbox', enabled: kind === 'draft' || kind === 'approved', run: skip },
      { keys: 'u', label: 'Withdraw approval', group: 'Outbox', enabled: kind === 'approved', run: () => void withdraw() },
      { keys: 'p', label: 'Pause or resume the company', group: 'Outbox', enabled: hasCompanyActions, run: togglePause },
      { keys: 'c', label: 'Close the company', group: 'Outbox', enabled: hasCompanyActions, run: companyActions.askClose },
      { keys: 'o', label: 'Open the company page', group: 'Outbox', run: () => navigate(`/companies/${company.id}`) },
    ],
    { selection: touchId },
  );
  useHotkeys([{ keys: 'escape', label: 'Leave the editor', group: 'Outbox', enabled: editing, run: leaveEditor }]);

  const onFix = (fix: CheckFix) => {
    if (fix.kind === 'compare') setComparing(fix.otherMessageId);
    else if (fix.kind === 'review_item') navigate(`/needs-you?sel=${fix.reviewId}`);
    else if (fix.kind === 'no_go') navigate('/no-go');
  };

  /* ------------------------------------------------------------ render */

  const pauseButton = (
    <Button type="button" variant="ghost" size="sm" onClick={togglePause}>
      {`${companyActions.held ? 'Resume' : 'Pause'} ${company.name}`}
      <Kbd aria-hidden>P</Kbd>
    </Button>
  );
  const companyButtons = hasCompanyActions ? (
    <>
      {pauseButton}
      <Button type="button" variant="ghost" size="sm" onClick={companyActions.askClose}>
        Close company
        <Kbd aria-hidden>C</Kbd>
      </Button>
    </>
  ) : null;

  let main: ReactNode;
  if (kind === 'reply') {
    main = (
      <ReplyDetail
        detail={detail}
        onReplyKind={onReplyKind}
        onClose={companyActions.askClose}
        onResume={companyActions.askResume}
        canResume={companyActions.held}
        canClose={hasCompanyActions}
      />
    );
  } else {
    const reviewable = kind === 'draft' || kind === 'approved';
    main = (
      <>
        <div className="grid flex-1 auto-rows-min content-start gap-4 overflow-y-auto px-5 py-4">
          {reviewable && item.seq > 0 ? (
            <FollowupTrack firstSentAt={detail.firstSentAt} followups={detail.followups} asOf={asOf} />
          ) : reviewable ? (
            <ContextRow detail={detail} />
          ) : (
            <>
              <StateLine detail={detail} />
              {detail.followups.length > 0 || detail.firstSentAt ? (
                <FollowupTrack firstSentAt={detail.firstSentAt} followups={detail.followups} asOf={asOf} />
              ) : null}
            </>
          )}
          {reviewable && detail.checks.length > 0 ? (
            <CheckList checks={detail.checks} onFix={onFix} roleKind={{ value: person.roleKind, onChange: setRoleKind }} />
          ) : null}
          {conflict ? (
            <StateBanner
              tone="warn"
              title="This email changed while you were editing"
              action={
                <div className="mt-1 flex gap-2">
                  <Button type="button" variant="outline" size="sm" onClick={() => void loadNewer()}>
                    Load newer
                  </Button>
                  <Button type="button" variant="outline" size="sm" onClick={() => void saveMine()}>
                    Save mine over it
                  </Button>
                </div>
              }
            >
              Claude or another tab saved a newer version. Your text is still here.
            </StateBanner>
          ) : null}
          {kind === 'draft' ? (
            <div
              onFocus={() => setEditing(true)}
              onBlur={(e) => {
                if (!e.currentTarget.contains(e.relatedTarget)) setEditing(false);
              }}
            >
              <DraftEditor
                subject={subject}
                body={body}
                onChange={(v) => setEdit({ ...v, base: edit?.base ?? item.revision })}
                spans={detail.checks.flatMap((c) => c.spans)}
                savedBy={detail.savedBy}
                savedAt={detail.savedAt}
                kind={person.roleKind}
                seq={item.seq}
                dirty={dirty}
              />
            </div>
          ) : kind === 'approved' ? (
            <>
              <ReadOnlyDraft subject={item.subject} body={detail.body} />
              <p className="text-xs text-muted-foreground">Withdraw the approval to edit this email.</p>
            </>
          ) : detail.thread.length > 0 ? (
            <Thread messages={detail.thread} asOf={asOf} />
          ) : (
            <ReadOnlyDraft subject={item.subject} body={detail.body} />
          )}
        </div>
        {kind === 'draft' ? (
          <footer className="flex flex-none flex-wrap items-center gap-2 border-t bg-background px-5 py-3">
            <Tooltip>
              <TooltipTrigger asChild>
                {/* aria-disabled, not disabled: it stays focusable so the reason shows on hover and focus. */}
                <Button
                  type="button"
                  aria-disabled={gateText ? true : undefined}
                  onClick={tryApprove}
                  className="aria-disabled:cursor-not-allowed aria-disabled:opacity-50 aria-disabled:hover:bg-primary"
                >
                  <CheckIcon aria-hidden />
                  Approve
                  <Kbd aria-hidden>A</Kbd>
                </Button>
              </TooltipTrigger>
              {gateText ? <TooltipContent>{gateText}</TooltipContent> : null}
            </Tooltip>
            <Button type="button" variant="outline" disabled={!dirty} onClick={() => void save()}>
              Save
              <Kbd aria-hidden>⌘S</Kbd>
            </Button>
            <Button type="button" variant="ghost" onClick={skip}>
              {item.seq > 0 ? 'Skip this follow-up' : 'Skip'}
              <Kbd aria-hidden>S</Kbd>
            </Button>
            <span className="ml-auto flex gap-1">{companyButtons}</span>
          </footer>
        ) : null}
        {kind === 'approved' ? (
          <footer className="flex flex-none flex-wrap items-center gap-2 border-t bg-background px-5 py-3">
            <Button type="button" onClick={() => void withdraw()}>
              <Undo2 aria-hidden />
              Withdraw approval
              <Kbd aria-hidden>U</Kbd>
            </Button>
            <Button type="button" variant="ghost" onClick={skip}>
              Skip
              <Kbd aria-hidden>S</Kbd>
            </Button>
            <span className="ml-auto flex gap-1">{companyButtons}</span>
          </footer>
        ) : null}
        {kind === 'sent' && hasCompanyActions ? (
          <footer className="flex flex-none items-center gap-2 border-t bg-background px-5 py-3">
            <span className="ml-auto flex gap-1">{companyButtons}</span>
          </footer>
        ) : null}
      </>
    );
  }

  return (
    <article ref={articleRef} aria-label={`Email to ${first}`} className="flex h-full min-h-0 flex-col">
      <DetailHeader detail={detail} />
      {main}
      {companyActions.dialogs}
      {comparing !== null ? (
        <CompareDialog messageId={messageId} otherMessageId={comparing} open onOpenChange={(o) => (o ? undefined : setComparing(null))} />
      ) : null}
    </article>
  );
}

/** Spec 5.3, the detail pane: the selected email, its checks and its actions. */
export function OutboxDetailPane({ touchId, items }: { touchId: Id; items: OutboxItem[] }) {
  const client = useClient();
  const query = useViewQuery(queryKeys.outboxDetail(touchId), () => client.outboxDetail(touchId), { pollMs: pollMs.outbox });
  return (
    <QueryState query={query} skeleton={<DetailSkeleton />} empty={null}>
      {(detail) => <DetailBody key={detail.item.messageId} detail={detail} items={items} />}
    </QueryState>
  );
}
