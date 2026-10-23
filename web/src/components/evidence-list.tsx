import { Lock } from 'lucide-react';
import type { Confidence, Fact, FactKind, SourceKind } from '@/api';
import { ConfidenceMeter } from '@/components/confidence-meter';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { formatDueDay } from '@/lib/format';
import { safeHref } from '@/lib/safe-href';

const FACT_LABELS: Record<FactKind, string> = {
  size: 'Size',
  stage: 'Stage',
  sponsorship: 'Sponsorship',
  domain: 'Email domain',
  hq: 'HQ',
  everify: 'E-Verify',
  lca: 'Labor filings',
  cap_exempt: 'Cap-exempt',
  staffing_risk: 'Staffing risk',
  funding: 'Funding',
  headcount: 'Headcount',
};

const SOURCE_LABELS: Record<SourceKind, string> = {
  hand: 'You',
  posting: 'Posting',
  old_dashboard: 'Old dashboard',
  yc: 'Y Combinator',
  builtin: 'Built In',
  linkedin: 'LinkedIn',
  website: 'Website',
  formd: 'SEC Form D',
  lca: 'Labor filings',
  everify: 'E-Verify',
  other: 'Other',
  stapply: 'Stapply',
  commoncrawl: 'Common Crawl',
  hn: 'Hacker News',
  simplify: 'Simplify',
};

const CONFIDENCE_WORDS: Record<Confidence, string> = { confirmed: 'High', estimate: 'Medium', guess: 'Guess' };

function Source({ fact }: { fact: Fact }) {
  const text = fact.sourceKind === 'hand' ? SOURCE_LABELS.hand : fact.sourceRef || SOURCE_LABELS[fact.sourceKind];
  const href = safeHref(fact.sourceRef);
  return (
    <>
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noreferrer"
          className="underline decoration-border underline-offset-3 hover:decoration-current"
        >
          {SOURCE_LABELS[fact.sourceKind]}
        </a>
      ) : (
        text
      )}
      {fact.observedOn ? <span className="text-muted-foreground">, {formatDueDay(fact.observedOn)}</span> : null}
    </>
  );
}

/** The facts behind a company, each with where it came from, the quote, and how sure we are. */
export function EvidenceList({ facts }: { facts: Fact[] }) {
  if (facts.length === 0) {
    return <p className="px-3 py-6 text-sm text-muted-foreground">No evidence yet for this company.</p>;
  }
  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Fact</TableHead>
            <TableHead>Value</TableHead>
            <TableHead>Source</TableHead>
            <TableHead>Quote</TableHead>
            <TableHead>Confidence</TableHead>
            <TableHead className="w-px" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {facts.map((fact) => (
            <TableRow key={fact.id}>
              <TableCell>{FACT_LABELS[fact.fact]}</TableCell>
              <TableCell className="font-medium">{fact.value}</TableCell>
              <TableCell className="text-[13px]">
                <Source fact={fact} />
              </TableCell>
              <TableCell className="max-w-72 text-[13px] whitespace-normal text-muted-foreground">
                {fact.quote ? `"${fact.quote}"` : ''}
              </TableCell>
              <TableCell>
                <span className="inline-flex items-center gap-1.5">
                  <ConfidenceMeter confidence={fact.confidence} />
                  {CONFIDENCE_WORDS[fact.confidence]}
                </span>
              </TableCell>
              <TableCell>
                {fact.locked ? (
                  <Badge variant="idle">
                    <Lock aria-hidden />
                    Locked
                  </Badge>
                ) : null}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
