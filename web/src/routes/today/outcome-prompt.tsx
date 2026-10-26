import { useId, useState } from 'react';
import { ChevronRight } from 'lucide-react';
import { pollMs, queryKeys, usePageClient, type Channel, type PromptTouch } from '@/api';
import { ReplyKindDialog } from '@/components/reply-kind-dialog';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { useHotkeys, type Binding } from '@/keyboard/hotkeys';
import { useViewQuery } from '@/pending/pending';
import { KeyHint } from './plan-panel';
import { formatMonthDay } from './short-dates';
import { usePlanActions } from './use-plan-actions';

/** What was sent, as a noun phrase: "a request", "an email". */
const SENT: Record<Channel, string> = {
  linkedin_request: 'a request',
  linkedin_message: 'a message',
  email: 'an email',
  application: 'an application',
  hn_reply: 'a reply on Hacker News',
  other: 'a message',
};

/** "Owen Brandt at Warp", "your application at Warp" (an application has no person), or the company alone. */
function whoOf(t: PromptTouch): string {
  if (t.person) return `${t.person.name} at ${t.company.name}`;
  return t.channel === 'application' ? `your application at ${t.company.name}` : t.company.name;
}

/** Where a reply came from, for the dialog: "Owen Brandt on LinkedIn", "Owen Brandt by email". */
function replyFrom(t: PromptTouch): string {
  const name = t.person?.name ?? t.company.name;
  if (t.channel === 'linkedin_request' || t.channel === 'linkedin_message') return `${name} on LinkedIn`;
  return t.channel === 'email' ? `${name} by email` : name;
}

const trigger =
  'group flex w-full items-center gap-2 rounded-sm py-2 text-left text-sm font-medium outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50';

/**
 * The 7-day question (design 16.1): one touch at a time, the oldest first, with Accepted (1), Replied (2) and Nothing
 * yet (3). Open on arrival, as Devanshu asked of the mockup. Nothing yet is a normal answer, so it is the same size
 * as the others and no count here is ever red. The keys act on the question that is shown, so a folded section or an
 * open dialog answers nothing (the dialog stops single keys itself). Absent when no touch is waiting.
 */
export function OutcomePrompt() {
  const client = usePageClient('plan');
  const actions = usePlanActions();
  const prompt = useViewQuery(queryKeys.outcomePrompt, () => client.outcomePrompt(), { pollMs: pollMs.default });
  const [open, setOpen] = useState(true);
  const [replying, setReplying] = useState<PromptTouch | null>(null);
  const headingId = useId();

  // Oldest first, by instant. The API lists them; the order he is asked in is only a reading order.
  const touches = [...(prompt.data?.touches ?? [])].sort((a, b) => Date.parse(a.sentAt) - Date.parse(b.sentAt));
  const current = touches[0];
  const shown = open && current !== undefined;

  const group = 'Today';
  const bindings: Binding[] = [
    { keys: '1', label: 'Answer: Accepted', group, enabled: shown, run: () => current && actions.answer(current, 'accepted') },
    { keys: '2', label: 'Answer: Replied', group, enabled: shown, run: () => current && setReplying(current) },
    { keys: '3', label: 'Answer: Nothing yet', group, enabled: shown, run: () => current && actions.answer(current, 'quiet') },
  ];
  useHotkeys(bindings);

  if (!current) return null;
  const more = touches.length - 1;
  return (
    <section aria-labelledby={headingId} className="border-t pt-1">
      <Collapsible open={open} onOpenChange={setOpen}>
        <h3 id={headingId}>
          <CollapsibleTrigger className={trigger}>
            <ChevronRight aria-hidden className="size-4 text-muted-foreground transition-transform group-data-[state=open]:rotate-90" />
            Last week, what happened?
            <span data-testid="count" className="font-normal text-muted-foreground tabular-nums">
              {touches.length}
            </span>
          </CollapsibleTrigger>
        </h3>
        <CollapsibleContent>
          <div className="grid max-w-[62ch] gap-2 pb-3 pl-6">
            <p data-testid="question" className="text-sm">
              {`What happened with ${whoOf(current)}? You sent ${SENT[current.channel]} on ${formatMonthDay(current.sentAt)}.`}
            </p>
            {current.context ? <p className="text-[13px] text-muted-foreground">{current.context}</p> : null}
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="outline" size="sm" aria-keyshortcuts="1" onClick={() => actions.answer(current, 'accepted')}>
                Accepted
                <KeyHint k="1" />
              </Button>
              <Button type="button" variant="outline" size="sm" aria-keyshortcuts="2" onClick={() => setReplying(current)}>
                Replied
                <KeyHint k="2" />
              </Button>
              <Button type="button" variant="outline" size="sm" aria-keyshortcuts="3" onClick={() => actions.answer(current, 'quiet')}>
                Nothing yet
                <KeyHint k="3" />
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              {more > 0 ? `${more} more after this one. ` : 'Last one. '}Nothing yet is a normal answer: it asks again in 7 days.
            </p>
          </div>
        </CollapsibleContent>
      </Collapsible>
      <ReplyKindDialog
        from={replying ? replyFrom(replying) : null}
        onClose={() => setReplying(null)}
        onPick={(kind) => {
          if (replying) actions.answer(replying, 'replied', kind);
          setReplying(null);
        }}
      />
    </section>
  );
}
