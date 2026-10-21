import { ChartColumn } from 'lucide-react';
import { EmptyState } from '@/components/state-views';

/** A titled stub so the route and its chord work; the progress ledger replaces it. */
export default function ProgressPage() {
  return (
    <div className="max-w-[1360px] px-6 pt-5 pb-12">
      <h1 className="sr-only">Progress</h1>
      <EmptyState icon={ChartColumn} title="Coming in this branch">
        The progress ledger lands here.
      </EmptyState>
    </div>
  );
}
