import { Award } from 'lucide-react';
import { EmptyState } from '@/components/state-views';

/** A titled stub so the route and its chord work; the win log replaces it. */
export default function WinsPage() {
  return (
    <div className="max-w-[1360px] px-6 pt-5 pb-12">
      <h1 className="sr-only">Wins</h1>
      <EmptyState icon={Award} title="Coming in this branch">
        The win log lands here.
      </EmptyState>
    </div>
  );
}
