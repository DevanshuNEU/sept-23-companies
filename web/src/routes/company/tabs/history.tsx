import type { CompanyView } from '@/api';
import { Timeline } from '@/components/timeline';
import { Card, CardContent } from '@/components/ui/card';
import { eventTone, sourceTag } from '../events';

/** Every event row, newest first as stored, each with where it came from. Imports are never dropped. */
export function History({ view }: { view: CompanyView }) {
  if (view.events.length === 0) return <p className="px-3 py-6 text-sm text-muted-foreground">No history yet for this company.</p>;
  return (
    <Card className="py-0">
      <CardContent className="py-3">
        <Timeline items={view.events.map((e) => ({ id: e.id, at: e.at, text: e.text, tone: eventTone(e), tag: sourceTag(e) }))} />
      </CardContent>
    </Card>
  );
}
