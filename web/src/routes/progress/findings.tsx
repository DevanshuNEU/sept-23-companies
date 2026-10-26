import { ChartColumn, ChevronRight } from 'lucide-react';
import { pollMs, queryKeys, usePageClient, type Finding } from '@/api';
import { EmptyState, QueryState } from '@/components/state-views';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Skeleton } from '@/components/ui/skeleton';
import { formatMonthName } from '@/lib/format';
import { formatRate, rateEnough } from '@/lib/progress';
import { useViewQuery } from '@/pending/pending';
import { formatDateMonthDay, formatMonthDay } from '@/routes/today/short-dates';

/** One finding: the API's sentence as given, each group's count with its rate (only from 20), a caution, what changed. */
function FindingCard({ finding }: { finding: Finding }) {
  return (
    <article className="grid gap-2 rounded-lg border p-4">
      <p className="max-w-[62ch] text-sm font-medium">{finding.text}</p>
      <table className="max-w-[420px] text-[13px] tabular-nums">
        <tbody>
          {finding.counts.map((c) => (
            <tr key={c.label} className="border-b last:border-b-0">
              <th scope="row" className="py-2 pr-6 text-left font-normal">{c.label}</th>
              <td className="py-2 text-right whitespace-nowrap">{formatRate(c.count, c.of, rateEnough(true, c.of))}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {finding.caution ? <p className="text-[13px] text-muted-foreground">{finding.caution}</p> : null}
      {finding.changed ? (
        <p className="text-[13px]">
          <span className="text-muted-foreground">Changed:</span> {finding.changed}
        </p>
      ) : null}
    </article>
  );
}

/**
 * What the data says (design 16.3): the month's one or two findings from the outcome report, below the ledger. The
 * text, the counts and the caution are the API's; this only lays them out, and draws a rate only for a group of 20 or
 * more. Before the first report it says when findings arrive.
 */
export function Findings() {
  const client = usePageClient('progress');
  const query = useViewQuery(queryKeys.findings, () => client.findings(), { pollMs: pollMs.default });
  const v = query.data;
  const reported = v !== undefined && v.findings.length > 0 ? v.reportedOn : null;
  return (
    <section aria-labelledby="findings-h" className="grid max-w-[820px] gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="findings-h" className="text-sm font-semibold">What the data says</h2>
        {v && reported ? <span className="text-xs text-muted-foreground">{`${formatMonthName(v.month)}, reported ${formatDateMonthDay(reported)}`}</span> : null}
      </div>
      <QueryState
        query={query}
        skeleton={<Skeleton aria-hidden className="h-32 w-full" />}
        isEmpty={(d) => d.reportedOn === null || d.findings.length === 0}
        empty={
          <div className="rounded-lg border">
            <EmptyState icon={ChartColumn} title="The first findings arrive after your first month of outcomes.">
              Each month brings one or two plain findings, with their counts and what changed because of them.
            </EmptyState>
          </div>
        }
      >
        {(d) => (
          <div className="grid gap-3">
            {d.findings.map((f) => (
              <FindingCard key={f.id} finding={f} />
            ))}
            {d.changes.length > 0 ? (
              <Collapsible className="rounded-lg border">
                <CollapsibleTrigger className="group flex w-full items-center gap-1.5 rounded-lg px-3 py-2.5 text-[13px] outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">
                  <ChevronRight aria-hidden className="size-3.5 text-muted-foreground transition-transform group-data-[state=open]:rotate-90" />
                  Earlier changes <span className="text-muted-foreground tabular-nums">{d.changes.length}</span>
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <ul className="grid gap-1.5 border-t px-4 py-3 text-[13px]">
                    {[...d.changes]
                      .sort((a, b) => Date.parse(b.at) - Date.parse(a.at))
                      .map((c) => (
                        <li key={`${c.at}${c.text}`}>{`${formatMonthDay(c.at)}: ${c.text}`}</li>
                      ))}
                  </ul>
                </CollapsibleContent>
              </Collapsible>
            ) : null}
          </div>
        )}
      </QueryState>
    </section>
  );
}
