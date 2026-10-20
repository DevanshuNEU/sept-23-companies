import { Copy, ExternalLink } from 'lucide-react';
import type { CompanyView } from '@/api';
import { Timeline } from '@/components/timeline';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { formatAge, formatDay } from '@/lib/format';
import { eventTone } from '../events';

const STANDS_COUNT = 8;
const PEOPLE_SHOWN = 5;

export function Overview({ view, onCopyRole, onOpenHistory }: { view: CompanyView; onCopyRole: () => void; onOpenHistory: () => void }) {
  // Where it stands reads in words, so the routine board reads stay in History.
  const events = view.events.filter((e) => e.kind !== 'board_read').slice(0, STANDS_COUNT);
  const byEmail = view.people.filter((p) => p.channel === 'email').length;
  const onLinkedIn = view.people.length - byEmail;
  const role = view.role;
  const posting = role ? view.postings.find((p) => p.id === role.postingId) : undefined;
  return (
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
      <Card className="gap-0 py-0">
        <CardHeader className="flex items-center border-b px-4 py-3 [.border-b]:pb-3">
          <CardTitle className="text-sm">Where it stands</CardTitle>
          <Button variant="link" size="sm" className="ml-auto h-auto p-0 text-xs text-muted-foreground" onClick={onOpenHistory}>
            Full history
          </Button>
        </CardHeader>
        <CardContent className="px-4 py-3">
          {events.length === 0 ? (
            <p className="py-4 text-sm text-muted-foreground">Nothing has happened here yet.</p>
          ) : (
            <Timeline
              items={events.map((e) => ({
                id: e.id,
                at: e.at,
                text: e.text,
                tone: eventTone(e),
                ...(e.importSource ? { tag: 'Imported' } : {}),
              }))}
            />
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4">
        <section aria-label="Role to name" className="grid gap-1.5 rounded-md border p-3">
          <span className="text-xs text-muted-foreground">Role to name</span>
          {role ? (
            <>
              <span className="text-sm font-semibold">{role.title}</span>
              <span className="text-xs text-muted-foreground">
                {`${role.location}. Posted ${formatDay(role.firstSeenAt)}, ${formatAge(role.firstSeenAt, view.asOf)}.${posting?.state === 'open' ? ' Still open.' : ''}`}
              </span>
              <div className="mt-1 flex gap-2">
                <Button variant="outline" size="sm" onClick={onCopyRole}>
                  <Copy aria-hidden />
                  Copy title
                </Button>
                <Button variant="ghost" size="sm" asChild>
                  <a href={role.url} target="_blank" rel="noreferrer">
                    <ExternalLink aria-hidden />
                    Open posting
                  </a>
                </Button>
              </div>
            </>
          ) : (
            <span className="text-sm text-muted-foreground">No open role matches the new grad rules right now.</span>
          )}
        </section>

        <Card className="gap-0 py-0">
          <CardHeader className="flex items-center border-b px-4 py-3 [.border-b]:pb-3">
            <CardTitle className="text-sm">People</CardTitle>
            <span className="ml-auto text-xs text-muted-foreground">{`${byEmail} by email, ${onLinkedIn} on LinkedIn`}</span>
          </CardHeader>
          {view.people.length === 0 ? (
            <p className="px-4 py-4 text-sm text-muted-foreground">No people yet. Paste them in the side terminal.</p>
          ) : (
            <ul>
              {view.people.slice(0, PEOPLE_SHOWN).map((p) => (
                <li key={p.person.id} className="flex items-center justify-between gap-3 border-b px-4 py-2 text-sm last:border-b-0">
                  <span className="min-w-0 truncate">
                    <span className="font-medium">{p.person.name}</span> <span className="text-muted-foreground">{p.person.title}</span>
                  </span>
                  <Badge variant={p.tone}>{p.statusLine}</Badge>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
