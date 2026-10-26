import { Component, type ReactNode } from 'react';
import { TriangleAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';

type Props = { name: string; onRetry: () => void; children: ReactNode };

/** The partial state of one tab: what did not load, and Retry. The header and the other tabs keep working. */
export function TabFailed({ name, onRetry }: { name: string; onRetry: () => void }) {
  return (
    <div role="alert" className="flex items-center gap-3 rounded-md border border-warn-bd bg-warn-bg px-4 py-3 text-sm text-warn">
      <TriangleAlert aria-hidden className="size-4 shrink-0" />
      <span>{`${name} did not load for this company.`}</span>
      <Button variant="outline" size="sm" className="ml-auto" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}

/**
 * One boundary per tab, so a tab that cannot render shows the partial state (design section 7) while the
 * header, the facts and the other tabs keep working. Retry refetches the company and tries the tab again.
 */
export class TabBoundary extends Component<Props, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  retry = () => {
    this.props.onRetry();
    this.setState({ failed: false });
  };

  render() {
    if (!this.state.failed) return this.props.children;
    return <TabFailed name={this.props.name} onRetry={this.retry} />;
  }
}
