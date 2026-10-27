import { useId, useState } from 'react';
import type { LinkedInPerson } from '@/api';
import { Input } from '@/components/ui/input';
import { formatDay } from '@/lib/format';
import type { Recorded } from './recorded';

const MAX_SHOWN = 8;

/**
 * "Mark someone as replied": he types a name or a company and picks the person from the people he messaged. Picking
 * only chooses; the reply kind is asked next and the write is the list's own reply log.
 */
export function MarkReplied({ recorded, onPick }: { recorded: Recorded[]; onPick: (r: Recorded) => void }) {
  const [text, setText] = useState('');
  const listId = useId();
  const q = text.trim().toLowerCase();
  const hits =
    q.length < 2
      ? []
      : recorded.filter(({ group, person }) =>
          [person.person.name, group.company.name].some((s) => s.toLowerCase().includes(q)),
        );
  const requested = (p: LinkedInPerson) => p.steps.find((s) => s.step === 'request_sent')?.at;
  return (
    <section aria-label="Mark someone as replied" className="mb-4 rounded-lg border bg-card p-3">
      <label htmlFor={`${listId}-q`} className="text-sm font-medium">
        Mark someone as replied
      </label>
      <p className="mt-0.5 text-[13px] text-muted-foreground">
        Type a name or a company, then pick the person you messaged who answered.
      </p>
      <Input
        id={`${listId}-q`}
        className="mt-2 h-9 max-w-[360px]"
        placeholder="Name or company"
        value={text}
        onChange={(e) => setText(e.target.value)}
        aria-controls={listId}
      />
      <ul id={listId} aria-label="People you messaged" className="mt-2 flex flex-col gap-1">
        {hits.slice(0, MAX_SHOWN).map((r) => {
          const at = requested(r.person);
          return (
            <li key={r.person.person.id}>
              <button
                type="button"
                className="w-full rounded-md px-2 py-1.5 text-left text-sm outline-none hover:bg-muted focus-visible:ring-[3px] focus-visible:ring-ring/50"
                onClick={() => {
                  onPick(r);
                  setText('');
                }}
              >
                <span className="font-medium">{r.person.person.name}</span>
                <span className="text-muted-foreground">
                  {` at ${r.group.company.name}`}
                  {at ? `, requested ${formatDay(at)}` : ''}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {q.length >= 2 && hits.length === 0 ? (
        <p className="mt-1 text-[13px] text-muted-foreground">Nobody you messaged matches &quot;{text.trim()}&quot;.</p>
      ) : null}
      {hits.length > MAX_SHOWN ? (
        <p className="mt-1 text-[13px] text-muted-foreground">{`${hits.length - MAX_SHOWN} more match. Type more to narrow it.`}</p>
      ) : null}
    </section>
  );
}
