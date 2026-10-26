import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate, useParams } from 'react-router';
import { usePanelRef } from 'react-resizable-panels';
import { Mail, X } from 'lucide-react';
import { pollMs, queryKeys, usePageClient, type Id, type OutboxView, type SendingStatus, type ShellSummary } from '@/api';
import { EmptyState, QueryState, StateBanner } from '@/components/state-views';
import { Button } from '@/components/ui/button';
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable';
import { useSelectionSync } from '@/keyboard/hotkeys';
import { useUrlState } from '@/lib/url-state';
import { useEdits } from '@/pending/edits';
import { usePendingWrite, useViewQuery, type Patch } from '@/pending/pending';
import { OutboxList, OutboxListSkeleton } from './outbox-list';
import { OutboxDetailPane } from './outbox-detail';
import { forCompany } from './outbox-patches';
import { SendingStrip, SendingStripSkeleton } from './sending-strip';

const SPLIT_KEY = 'outreach.outbox.split';
const LIST_PX = 400;
const LIST_MIN_PX = 300;

/** The list width he last dragged to, in pixels, or the spec's 400. */
function savedListWidth(): number {
  const px = Number(window.localStorage.getItem(SPLIT_KEY));
  return Number.isFinite(px) && px >= LIST_MIN_PX ? px : LIST_PX;
}

function idOf(param: string | undefined): Id | null {
  const id = Number(param);
  return param !== undefined && Number.isInteger(id) && id > 0 ? id : null;
}

/** Optimistic sender state for the strip and the sidebar footer, until the refetch after the write lands. */
function senderPatches(patch: Partial<SendingStatus>): Patch[] {
  const outbox: Patch<OutboxView> = { key: queryKeys.outbox, apply: (v) => ({ ...v, sending: { ...v.sending, ...patch } }) };
  const shell: Patch<ShellSummary> = {
    key: queryKeys.shell,
    apply: (s) => (patch.state ? { ...s, sending: { ...s.sending, state: patch.state } } : s),
  };
  return [outbox, shell];
}

/** The alert above the list when Gmail stopped the sender (spec 5.3, Outbox states). */
function SenderAlert({ s }: { s: SendingStatus }) {
  if (s.state === 'paused_hard_block') {
    return (
      <StateBanner tone="bad" title={`Sending paused: ${s.pausedReason}.`}>
        {s.gmailMessage ? <span className="font-mono text-xs break-words">{s.gmailMessage}</span> : null}
      </StateBanner>
    );
  }
  if (s.state === 'signed_out') {
    return (
      <StateBanner tone="bad" title="Gmail is signed out">
        {`${s.pausedReason} Approve still works: approved emails wait until Gmail is back.`}
      </StateBanner>
    );
  }
  return null;
}

/** The bar above the list while `?company=` is set: whose emails these are, and the way out. */
function CompanyFilterBar({ name, onClear }: { name: string | null; onClear: () => void }) {
  return (
    <div className="flex flex-none items-center gap-2 border-b bg-subtle px-4 py-1.5 text-sm">
      <span>{name ? `Showing only ${name}` : 'Showing only this company'}</span>
      <Button type="button" variant="ghost" size="xs" onClick={onClear}>
        <X aria-hidden />
        Show all companies
      </Button>
    </div>
  );
}

function DetailPlaceholder() {
  return (
    <EmptyState icon={Mail} title="No email selected" hint="J">
      Move down the list to open one.
    </EmptyState>
  );
}

/** Spec 5.3: the sending strip, then the list and the selected email side by side. */
export default function OutboxPage() {
  const client = usePageClient('outbox');
  const outbox = useViewQuery(queryKeys.outbox, () => client.outbox(), { pollMs: pollMs.outbox });
  const edits = useEdits();
  const queryClient = useQueryClient();
  // An email that left drafted on the server (sent, skipped, approved elsewhere) drops its unsaved text; the raw
  // cache is read, not the view, so an optimistic skip still inside its undo window keeps the text for Undo.
  useEffect(() => {
    const raw = queryClient.getQueryData<OutboxView>(queryKeys.outbox);
    if (!raw) return;
    const states = new Map(raw.items.map((i) => [i.messageId, i.state] as const));
    edits.prune((id) => states.get(id));
  }, [outbox.dataUpdatedAt, queryClient, edits]);
  const write = usePendingWrite();
  const navigate = useNavigate();
  const { search } = useLocation();
  const selected = idOf(useParams().touchId);
  const [companyParam, setCompanyParam] = useUrlState('company', '');
  const company = idOf(companyParam);
  const [listPx] = useState(savedListWidth);
  const listPanel = usePanelRef();

  // Reports the selection the URL shows; the list records each move at once, so keys follow it (see useHotkeys).
  useSelectionSync(selected);
  const select = (touchId: Id) => navigate({ pathname: `/outbox/${touchId}`, search }, { replace: true });

  // Pausing and resuming commit at once: a pause that waited 8 seconds could let an email out.
  const pauseSending = () =>
    write({
      id: 'sending:pause',
      label: 'Sending paused. Nothing sends until you resume.',
      afterCommit: 'Sending paused.',
      immediate: true,
      patches: senderPatches({ state: 'paused_by_you', pausedReason: 'Paused by you', nextSend: null, noneReason: 'Sending is paused until you resume.' }),
      commit: (o) => client.pauseSending(o),
    });
  const resumeSending = (acknowledgeBlock: boolean) =>
    write({
      id: 'sending:resume',
      label: 'Sending resumed.',
      afterCommit: 'Sending resumed.',
      immediate: true,
      patches: senderPatches({ state: 'running', pausedReason: '', gmailMessage: null }),
      commit: (o) => client.resumeSending(acknowledgeBlock, o),
    });

  const saveSplit = (userMoved: boolean) => {
    const px = listPanel.current?.getSize().inPixels;
    if (userMoved && px) window.localStorage.setItem(SPLIT_KEY, String(Math.round(px)));
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <h1 className="sr-only">Outbox</h1>
      <QueryState
        query={outbox}
        skeleton={
          <>
            <SendingStripSkeleton />
            <OutboxListSkeleton />
          </>
        }
        empty={null}
      >
        {(all) => {
          const view = company === null ? all : forCompany(all, company);
          const filterName = company === null ? undefined : (all.items.find((i) => i.company.id === company)?.company.name ?? null);
          return (
            <div className="flex h-full min-h-0 flex-col">
              <SendingStrip
                sending={view.sending}
                onPause={pauseSending}
                onResume={() => resumeSending(view.sending.state === 'paused_hard_block')}
              />
              {view.sending.state === 'paused_hard_block' || view.sending.state === 'signed_out' ? (
                <div className="flex-none border-b px-4 py-3">
                  <SenderAlert s={view.sending} />
                </div>
              ) : null}
              {filterName !== undefined ? <CompanyFilterBar name={filterName} onClear={() => setCompanyParam('')} /> : null}
              {filterName !== undefined && view.items.length === 0 ? (
                <EmptyState
                  icon={Mail}
                  title="Nothing in the Outbox for this company"
                  action={
                    <Button type="button" variant="outline" size="sm" onClick={() => setCompanyParam('')}>
                      Show all companies
                    </Button>
                  }
                >
                  No drafts, replies or sent emails yet.
                </EmptyState>
              ) : (
                <ResizablePanelGroup
                  orientation="horizontal"
                  className="min-h-0 flex-1"
                  onLayoutChanged={(_, meta) => saveSplit(meta.isUserInteraction)}
                >
                  <ResizablePanel id="list" panelRef={listPanel} defaultSize={listPx} minSize={LIST_MIN_PX} maxSize="60%">
                    <OutboxList
                      // A new filter is a new list: rows it brings in are not "new", and nothing is collapsed by the last one.
                      key={company ?? 'all'}
                      items={view.items}
                      selected={selected}
                      onSelect={select}
                      asOf={view.asOf}
                      counts={view.counts}
                      closedBreakdown={view.closedBreakdown}
                      sentToday={view.sending.sentToday}
                    />
                  </ResizablePanel>
                  <ResizableHandle />
                  <ResizablePanel id="detail" minSize={360}>
                    {selected === null || !view.items.some((i) => i.touchId === selected) ? (
                      <div className="h-full overflow-y-auto">
                        <DetailPlaceholder />
                      </div>
                    ) : (
                      <OutboxDetailPane key={selected} touchId={selected} items={view.items} />
                    )}
                  </ResizablePanel>
                </ResizablePanelGroup>
              )}
            </div>
          );
        }}
      </QueryState>
    </div>
  );
}
