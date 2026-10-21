import type { CompanyView } from '@/api';
import { EvidenceList } from '@/components/evidence-list';

export function Evidence({ view }: { view: CompanyView }) {
  return (
    <div className="grid gap-2">
      <EvidenceList facts={view.evidence} />
      <p className="text-xs text-muted-foreground">Values you set by hand win over anything read later, and stay locked.</p>
    </div>
  );
}
