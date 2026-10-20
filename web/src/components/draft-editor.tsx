import { useId, useState, type ReactNode } from 'react';
import type { Check, IsoTime, RoleKind, SavedBy } from '@/api';
import { formatLocalTime } from '@/lib/format';
import { cn } from '@/lib/utils';

// The same counting rules as mail/check_draft.py: words skip full URLs, links are full URLs and bare domains Gmail would link, and an email address is not a link.
const URL_RE = /https?:\/\/[^\s<>()"']+/gi;
const EMAIL_RE = /[\w.+-]+@[\w-]+(\.[\w-]+)+/g;
const BARE_RE =
  /(?<![\w@./-])(?:[a-z0-9-]+\.)+(?:com|ai|io|dev|app|co|org|net|ly|gl|in|at|cc|id|me|sh|so|xyz|us)\b(?:\/[^\s<>()"']*)?/gi;

function countWords(body: string): number {
  return body.replace(URL_RE, ' ').split(/\s+/).filter(Boolean).length;
}

function countLinks(body: string): number {
  const text = body.replace(EMAIL_RE, ' ');
  const rest = text.replace(URL_RE, (m) => ' '.repeat(m.length));
  return (text.match(URL_RE)?.length ?? 0) + (rest.match(BARE_RE)?.length ?? 0);
}

/** The word range from check_draft.py: 80 to 120 (80 to 100 for a recruiter), up to 80 for a follow-up. Display only. */
function wordRange(kind: RoleKind, seq: number): [number, number] {
  if (seq > 0) return [0, 80];
  return kind === 'recruiter' ? [80, 100] : [80, 120];
}

const WHO: Record<SavedBy, string> = { claude: 'Claude', devanshu: 'you', sender: 'the sender', import: 'import' };

type Piece = { text: string; mark: boolean };

/** Split text into plain and marked pieces. Spans of one field are clamped to the text and merged where they overlap. */
function pieces(text: string, spans: Check['spans'], field: 'subject' | 'body'): Piece[] {
  const ranges = spans
    .filter((s) => s.field === field)
    .map((s) => ({ start: Math.max(0, s.start), end: Math.min(text.length, s.end) }))
    .filter((r) => r.end > r.start)
    .sort((a, b) => a.start - b.start);
  const merged: { start: number; end: number }[] = [];
  for (const r of ranges) {
    const last = merged[merged.length - 1];
    if (last && r.start <= last.end) last.end = Math.max(last.end, r.end);
    else merged.push({ ...r });
  }
  const out: Piece[] = [];
  let at = 0;
  for (const r of merged) {
    if (r.start > at) out.push({ text: text.slice(at, r.start), mark: false });
    out.push({ text: text.slice(r.start, r.end), mark: true });
    at = r.end;
  }
  if (at < text.length) out.push({ text: text.slice(at), mark: false });
  return out;
}

/** The mirrored layer: the same text in the same box, invisible, with a visible mark behind the flagged spans. */
function Marks({ parts }: { parts: Piece[] }): ReactNode {
  return parts.map((p, i) =>
    p.mark ? (
      <mark key={i} className="rounded-sm bg-warn-bd/60 text-transparent underline decoration-warn decoration-wavy underline-offset-2">
        {p.text}
      </mark>
    ) : (
      <span key={i}>{p.text}</span>
    ),
  );
}

/** Spec 5.3, Editor: a subject, a plain-text body, and a meter. Counts are display only; the stored checks come from the API after a save. */
export function DraftEditor({
  subject,
  body,
  onChange,
  spans,
  savedBy,
  savedAt,
  kind,
  seq,
  dirty,
}: {
  subject: string;
  body: string;
  onChange: (v: { subject: string; body: string }) => void;
  spans: Check['spans'];
  savedBy: SavedBy;
  savedAt: IsoTime;
  kind: RoleKind;
  seq: 0 | 1 | 2 | 3;
  dirty: boolean;
}) {
  const subjectId = useId();
  const [scrollTop, setScrollTop] = useState(0);
  const words = countWords(body);
  const links = countLinks(body);
  const [lo, hi] = wordRange(kind, seq);
  const inRange = words >= lo && words <= hi;
  return (
    <div className="overflow-hidden rounded-md border border-input bg-background focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/30">
      <div className="flex items-center gap-2 border-b px-3">
        <label htmlFor={subjectId} className="w-[52px] text-xs text-muted-foreground">
          Subject
        </label>
        <div className="relative flex-1">
          <div aria-hidden className="pointer-events-none absolute inset-0 flex items-center overflow-hidden text-sm font-medium whitespace-pre text-transparent">
            <span>
              <Marks parts={pieces(subject, spans, 'subject')} />
            </span>
          </div>
          <input
            id={subjectId}
            value={subject}
            onChange={(e) => onChange({ subject: e.target.value, body })}
            className="relative block h-[38px] w-full border-0 bg-transparent text-sm font-medium outline-none"
          />
        </div>
      </div>
      <div className="relative">
        <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
          <div
            className="p-3 text-sm leading-[22px] break-words whitespace-pre-wrap text-transparent"
            style={{ transform: `translateY(-${scrollTop}px)` }}
          >
            <Marks parts={pieces(body, spans, 'body')} />
            {/* A final newline needs a line of its own, as in a textarea. */}
            {body.endsWith('\n') ? '​' : null}
          </div>
        </div>
        <textarea
          aria-label="Email body"
          value={body}
          rows={18}
          onChange={(e) => onChange({ subject, body: e.target.value })}
          onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
          className="relative block min-h-[280px] w-full resize-y border-0 bg-transparent p-3 text-sm leading-[22px] outline-none"
        />
      </div>
      <div className="flex items-center gap-3.5 border-t bg-subtle px-3 py-2 text-xs text-muted-foreground">
        <span title={`Aim for ${lo === 0 ? 'up to' : `${lo} to`} ${hi}`} className={cn(!inRange && 'font-medium text-warn')}>
          {words === 1 ? '1 word' : `${words} words`}
        </span>
        <span>{links === 1 ? '1 link' : `${links} links`}</span>
        <span>Plain text</span>
        <span className="ml-auto flex gap-3.5">
          {dirty ? <span className="font-medium text-warn">Unsaved changes</span> : null}
          <span>{`Saved by ${WHO[savedBy]} ${formatLocalTime(savedAt)}`}</span>
        </span>
      </div>
    </div>
  );
}
