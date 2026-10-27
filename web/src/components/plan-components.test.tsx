/// <reference types="node" />
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { vi } from 'vitest';
import type { Check, Id, NoteDraft, PlanRow, PlanStep, PlanView } from '@/api';
import { buildFixture } from '@/api/fixture/data';
import { DayRail } from '@/components/day-rail';
import { NoteBlock } from '@/components/note-block';
import { SourceLinks } from '@/components/source-links';
import { StepMarks } from '@/components/step-marks';
import { WinMark } from '@/components/win-mark';
import type { RowProps } from '@/keyboard/list-nav';

// The default fixture's plan (Tue Nov 10, 10:14 in Boston): Dana and Tomas done, Lena first to do,
// Jonah's line only proposed, Cerebrium with nobody yet, Ines carried from Monday, Mintlify by email.
function plan(): PlanView {
  return structuredClone(buildFixture().plan);
}

function rowOf(p: PlanView, company: string): PlanRow {
  const row = p.rows.find((r) => r.company.name === company);
  if (!row) throw new Error(`No ${company} row in the fixture`);
  return row;
}

function noteOf(p: PlanView, company: string): NoteDraft {
  const note = rowOf(p, company).note;
  if (!note) throw new Error(`No note on the ${company} row`);
  return note;
}

function rail(p: PlanView, opts: { selected?: Id; onClick?: (id: Id) => void; allDone?: boolean } = {}) {
  const rowProps = (id: Id): RowProps => ({
    'aria-selected': id === opts.selected,
    'data-active': id === opts.selected,
    onClick: () => opts.onClick?.(id),
    ref: () => {},
  });
  return <DayRail rows={p.rows} rowProps={rowProps} tomorrow={p.tomorrow} allDone={opts.allDone ?? false} asOf={p.asOf} />;
}

const pass = (code: string, title: string): Check => ({ code, severity: 'pass', title, detail: '', fix: null, spans: [] });

function noteBlock(note: NoteDraft, extra: Partial<Parameters<typeof NoteBlock>[0]> = {}) {
  const calls = { copy: vi.fn(), edit: vi.fn(), confirm: vi.fn() };
  render(<NoteBlock note={note} onCopy={calls.copy} onEdit={calls.edit} onConfirm={calls.confirm} {...extra} />);
  return calls;
}

describe('DayRail', () => {
  it('names each row with its status word', () => {
    render(rail(plan()));
    const box = screen.getByRole('listbox', { name: "Today's people" });
    const names = within(box).getAllByRole('option').map((o) => o.getAttribute('aria-label'));
    expect(names).toEqual([
      '1. Dana Whitfield at Resend, Done',
      '2. Tomas Reyes at Baseten, Done',
      '3. Lena Ortiz at Pylon, To do',
      '4. Jonah Park at Inngest, To do, your line to confirm',
      '5. Cerebrium, To do',
      '6. Ines Duarte at Turso, To do, carried over from Monday',
      '7. Mintlify by email, To do',
    ]);
    // What the label says is also on screen: the monogram, the person and the company; a row still to do needs no word.
    const lena = screen.getByRole('option', { name: '3. Lena Ortiz at Pylon, To do' });
    expect(lena).toHaveTextContent('PLena OrtizPylon');
    expect(lena).not.toHaveTextContent('To do');
    expect(screen.getByRole('option', { name: /^6\. Ines Duarte/ })).toHaveTextContent('Carried over from Monday');
    expect(screen.getByRole('option', { name: /^4\. Jonah Park/ })).toHaveTextContent('Your line to confirm');
    expect(screen.getByRole('option', { name: /^7\. Mintlify/ })).toHaveTextContent('By email, in the Outbox');
  });

  it('a row with one step logged reads Logged with the working dot', () => {
    const p = plan();
    const lena = rowOf(p, 'Pylon');
    lena.steps = lena.steps.map((s): PlanStep => (s.kind === 'request' ? { ...s, at: '2026-11-10T15:02:00Z', touchId: 900 } : s));
    render(rail(p));
    const option = screen.getByRole('option', { name: '3. Lena Ortiz at Pylon, Logged' });
    expect(option).toHaveAttribute('data-node', 'logged');
  });

  it('draws each node by state: check when done, ring when selected, empty when to do', () => {
    const p = plan();
    render(rail(p, { selected: rowOf(p, 'Pylon').entryId }));
    expect(screen.getByRole('option', { name: /^1\. Dana/ })).toHaveAttribute('data-node', 'done');
    expect(screen.getByRole('option', { name: /^3\. Lena/ })).toHaveAttribute('data-node', 'current');
    expect(screen.getByRole('option', { name: /^3\. Lena/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('option', { name: /^4\. Jonah/ })).toHaveAttribute('data-node', 'todo');
    expect(screen.getByRole('option', { name: /^4\. Jonah/ })).toHaveAttribute('aria-selected', 'false');
  });

  it('the first todo has aria-current step', () => {
    const { container } = render(rail(plan()));
    const current = container.querySelectorAll('[aria-current]');
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveAttribute('aria-current', 'step');
    expect(current[0]).toHaveAttribute('aria-label', '3. Lena Ortiz at Pylon, To do');
  });

  it('a skipped row reads Carried over and has no red tone', () => {
    const p = plan();
    rowOf(p, 'Turso').state = 'skipped';
    render(rail(p));
    const option = screen.getByRole('option', { name: /^6\. Ines Duarte at Turso, Carried over/ });
    expect(option).toHaveAttribute('data-node', 'carried');
    expect(option).toHaveTextContent('Carried over');
    expect(option.outerHTML).not.toMatch(/\b(?:text|bg|border)-(?:bad|destructive)|\bred\b/);
  });

  it("the finish node shows tomorrow's text", () => {
    const { container } = render(rail(plan()));
    const finish = container.querySelector('[data-finish]');
    expect(finish).toHaveTextContent('Done for today');
    expect(finish).toHaveTextContent("Tomorrow's plan builds at 08:00. Room for 25 people.");
    expect(finish).toHaveAttribute('data-done', 'false');
    expect(finish).not.toHaveAttribute('aria-current');
    // The finish node is not a person: it is outside the listbox.
    expect(within(screen.getByRole('listbox')).queryByText('Done for today')).toBeNull();
  });

  it('with every row done the finish node fills and becomes the current step', () => {
    const p = plan();
    for (const r of p.rows) r.state = 'done';
    const { container } = render(rail(p, { allDone: true }));
    const finish = container.querySelector('[data-finish]');
    expect(finish).toHaveAttribute('data-done', 'true');
    expect(finish).toHaveAttribute('aria-current', 'step');
    expect(container.querySelectorAll('[aria-current]')).toHaveLength(1);
    expect(screen.getByRole('option', { name: '7. Mintlify by email, Done' })).toHaveTextContent('By email, reviewed');
  });

  it('a skipped row reads Carried over: the API skips a row only to carry it (a removed company is off the plan)', () => {
    const p = plan();
    const rows = p.rows.map((r) => (r.position === 4 ? { ...r, state: 'skipped' as const } : r));
    render(rail({ ...p, rows }));
    const box = screen.getByRole('listbox', { name: "Today's people" });
    expect(within(box).getAllByRole('option')[3]).toHaveAccessibleName('4. Jonah Park at Inngest, Carried over');
  });

  it('clicking a row calls its onClick', () => {
    const p = plan();
    const onClick = vi.fn();
    render(rail(p, { onClick }));
    fireEvent.click(screen.getByRole('option', { name: /^4\. Jonah Park/ }));
    expect(onClick).toHaveBeenCalledWith(rowOf(p, 'Inngest').entryId);
  });

  it('keeps every row in place when a row finishes', () => {
    const p = plan();
    const { rerender } = render(rail(p));
    const before = screen.getAllByRole('option').map((o) => o.getAttribute('aria-label')?.split(' at ')[0]);
    rowOf(p, 'Pylon').state = 'done';
    rerender(rail(p));
    const after = screen.getAllByRole('option').map((o) => o.getAttribute('aria-label')?.split(' at ')[0]);
    expect(after).toEqual(before);
    expect(screen.getByRole('option', { name: '3. Lena Ortiz at Pylon, Done' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /^4\. Jonah/ })).toHaveAttribute('aria-current', 'step');
  });

  it('says carried over with the date when the row is older than a week', () => {
    const p = plan();
    rowOf(p, 'Turso').carriedFrom = '2026-11-02';
    render(rail(p));
    expect(screen.getByRole('option', { name: '6. Ines Duarte at Turso, To do, carried over from Mon Nov 2' })).toBeInTheDocument();
  });
});

describe('NoteBlock', () => {
  it('sets the note he sends in the serif note face, on a card', () => {
    const note = { ...noteOf(plan(), 'Pylon'), chars: 100, maxChars: 300 };
    noteBlock(note);
    const text = screen.getByText(note.text);
    expect(text).toHaveClass('font-serif', 'text-[16px]', 'leading-[1.65]', 'max-w-[62ch]');
    // The card spans the whole block, so the text never sits in a narrower box than its surroundings.
    expect(text.parentElement).toHaveClass('bg-card');
  });

  it('shows the count with words', () => {
    noteBlock({ ...noteOf(plan(), 'Pylon'), chars: 168, maxChars: 300 });
    const count = screen.getByText('168 of 300 characters');
    expect(count).toHaveAttribute('data-tone', 'idle');
    expect(screen.getByRole('region', { name: 'Note to send' })).toBeInTheDocument();
  });

  it('past 90% of the limit the count is warn toned and says how many are left', () => {
    noteBlock({ ...noteOf(plan(), 'Pylon'), chars: 270, maxChars: 300 });
    expect(screen.getByText('270 of 300 characters', { selector: '[data-tone]' })).toHaveAttribute('data-tone', 'idle');
    cleanup();
    noteBlock({ ...noteOf(plan(), 'Pylon'), chars: 285, maxChars: 300 });
    const count = screen.getByText(/285 of 300 characters/, { selector: '[data-tone]' });
    expect(count).toHaveAttribute('data-tone', 'warn');
    expect(count).toHaveTextContent('285 of 300 characters, 15 left');
  });

  it('an over-limit note is bad toned and says "Over the limit by 12 characters"', () => {
    noteBlock({ ...noteOf(plan(), 'Pylon'), chars: 312, maxChars: 300 });
    const count = screen.getByText(/312 of 300 characters/, { selector: '[data-tone]' });
    expect(count).toHaveAttribute('data-tone', 'bad');
    expect(count).toHaveTextContent('Over the limit by 12 characters');
  });

  it('NoteBlock flags a fact without a safe link', () => {
    const note = noteOf(plan(), 'Pylon');
    const [first] = note.facts;
    if (!first) throw new Error('No fact on the Pylon note');
    first.source = { label: 'Pylon blog, Oct 14', url: 'javascript:alert(1)' };
    noteBlock(note);
    expect(screen.queryByRole('link', { name: 'Pylon blog, Oct 14' })).toBeNull();
    expect(screen.getByText('Pylon blog, Oct 14')).toBeInTheDocument();
    expect(screen.getByText('No link')).toBeInTheDocument();
    expect(document.querySelector('a[href^="javascript"]')).toBeNull();
  });

  it('NoteBlock shows facts with links before the actions', () => {
    const note = noteOf(plan(), 'Pylon');
    noteBlock(note);
    expect(screen.getByText('Facts used')).toBeInTheDocument();
    const copy = screen.getByRole('button', { name: 'Copy' });
    for (const f of note.facts) {
      expect(screen.getByText(new RegExp(f.text))).toBeInTheDocument();
      const link = screen.getByRole('link', { name: f.source.label });
      expect(link).toHaveAttribute('href', f.source.url);
      expect(link).toHaveAttribute('target', '_blank');
      expect(link).toHaveAttribute('rel', 'noopener noreferrer');
      expect(link.compareDocumentPosition(copy) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it('shows his line when it is his, and no question', () => {
    const note = noteOf(plan(), 'Pylon');
    noteBlock(note);
    expect(screen.getByText('Why them, in your words')).toBeInTheDocument();
    expect(screen.getByText(note.reasonLine)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Confirm line' })).toBeNull();
  });

  it('needsYourLine shows the proposed line with a Confirm line button (aria-keyshortcuts y)', () => {
    const note = noteOf(plan(), 'Inngest');
    expect(note.needsYourLine).toBe(true);
    const calls = noteBlock(note);
    expect(screen.getByText('Is this why you are writing to them? Confirm it or write your own.')).toBeInTheDocument();
    expect(screen.getByText(note.reasonLine)).toBeInTheDocument();
    const confirm = screen.getByRole('button', { name: 'Confirm line' });
    expect(confirm).toHaveAttribute('aria-keyshortcuts', 'y');
    expect(confirm).toHaveAttribute('data-variant', 'default');
    fireEvent.click(confirm);
    expect(calls.confirm).toHaveBeenCalledTimes(1);
    const own = screen.getByRole('button', { name: 'Write my own' });
    expect(own).toHaveAttribute('aria-keyshortcuts', 'e');
    fireEvent.click(own);
    expect(calls.edit).toHaveBeenCalledTimes(1);
    // One button per key: the foot has no second Edit while the line is asked for.
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    expect(screen.queryByText('Why them, in your words')).toBeNull();
  });

  it('his own note asks nothing about Claude\'s line, does not show it as his, and Copy runs past warnings', () => {
    // The Kabir case (#9, 2026-10-26): he saved his own text over a note whose line Claude proposed.
    const claude = noteOf(plan(), 'Inngest');
    const warn: Check = { code: 'number_not_linked', severity: 'warn', title: 'A number with no link behind it', detail: 'Add the source.', fix: null, spans: [] };
    const text = 'Hi Jonah, I build agent infra and would like to hear how your team ships.';
    const note: NoteDraft = { ...claude, text, chars: text.length, savedBy: 'devanshu', needsYourLine: false, checks: [pass('a', 'Under the limit'), warn] };
    expect(note.reasonState).toBe('proposed');
    const calls = noteBlock(note);
    expect(screen.queryByText('Is this why you are writing to them? Confirm it or write your own.')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Confirm line' })).toBeNull();
    expect(screen.queryByText('Why them, in your words')).toBeNull();
    expect(screen.queryByText(claude.reasonLine)).toBeNull();
    expect(screen.getByText(text)).toBeInTheDocument();
    expect(screen.getByText('A number with no link behind it')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('1 warning.');
    const copy = screen.getByRole('button', { name: 'Copy' });
    expect(copy).toHaveAttribute('aria-disabled', 'false');
    fireEvent.click(copy);
    expect(calls.copy).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument();
  });

  it('Copy and Edit buttons carry aria-keyshortcuts c and e', () => {
    const calls = noteBlock(noteOf(plan(), 'Pylon'));
    const copy = screen.getByRole('button', { name: 'Copy' });
    const edit = screen.getByRole('button', { name: 'Edit' });
    expect(copy).toHaveAttribute('aria-keyshortcuts', 'c');
    expect(edit).toHaveAttribute('aria-keyshortcuts', 'e');
    fireEvent.click(copy);
    fireEvent.click(edit);
    expect(calls.copy).toHaveBeenCalledTimes(1);
    expect(calls.edit).toHaveBeenCalledTimes(1);
  });

  it('says how many checks passed in the foot', () => {
    const note = { ...noteOf(plan(), 'Pylon'), checks: [pass('a', 'Under the limit'), pass('b', 'No banned phrases')] };
    noteBlock(note);
    expect(screen.getByRole('status')).toHaveTextContent('2 checks passed.');
    expect(screen.getByRole('button', { name: 'Copy' })).toHaveAttribute('aria-disabled', 'false');
  });

  it('a blocking check shows in the check list and Copy cannot run', () => {
    const block: Check = { code: 'note_too_long', severity: 'block', title: 'Note is too long', detail: 'Cut 12 characters.', fix: null, spans: [] };
    const calls = noteBlock({ ...noteOf(plan(), 'Pylon'), checks: [block, pass('b', 'No banned phrases')] });
    expect(within(screen.getByRole('group', { name: 'Check results' })).getByText('Note is too long')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('1 check blocks this note.');
    const copy = screen.getByRole('button', { name: 'Copy' });
    expect(copy).toHaveAttribute('aria-disabled', 'true');
    expect(copy).toHaveAccessibleDescription('1 check blocks this note.');
    fireEvent.click(copy);
    expect(calls.copy).not.toHaveBeenCalled();
  });

  it('while a save is pending the checks are unknown and Copy and Confirm line wait', () => {
    const calls = noteBlock(noteOf(plan(), 'Inngest'), { saving: true });
    expect(screen.getByRole('status')).toHaveTextContent('Saving. The checks run again before you can copy.');
    expect(screen.queryByText(/checks? passed/)).toBeNull();
    expect(screen.getByRole('button', { name: 'Copy' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('button', { name: 'Confirm line' })).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm line' }));
    expect(calls.copy).not.toHaveBeenCalled();
    expect(calls.confirm).not.toHaveBeenCalled();
  });

  it('an unsaved edit makes Copy wait and says why', () => {
    const calls = noteBlock(noteOf(plan(), 'Pylon'), { unsaved: true, editing: <textarea aria-label="Note to Lena Ortiz" defaultValue="draft" /> });
    const copy = screen.getByRole('button', { name: 'Copy' });
    expect(copy).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('status')).toHaveTextContent('Your edit is not saved. Copy waits for Save.');
    fireEvent.click(copy);
    expect(calls.copy).not.toHaveBeenCalled();
  });

  it('a waiting Copy keeps its focus: it is aria-disabled, never disabled', () => {
    noteBlock(noteOf(plan(), 'Inngest'), { saving: true });
    const copy = screen.getByRole('button', { name: 'Copy' });
    expect(copy).not.toHaveAttribute('disabled');
    copy.focus();
    expect(copy).toHaveFocus();
  });

  it('announces the count and the check result in one polite status region', () => {
    const note = { ...noteOf(plan(), 'Pylon'), chars: 168, maxChars: 300, checks: [pass('a', 'Under the limit'), pass('b', 'No banned phrases')] };
    const props = { note, onCopy: vi.fn(), onEdit: vi.fn(), onConfirm: vi.fn() };
    const { rerender } = render(<NoteBlock {...props} saving />);
    const region = screen.getByRole('status');
    expect(region).toHaveAttribute('aria-live', 'polite');
    expect(region).toHaveAttribute('aria-atomic', 'true');
    expect(region).toHaveTextContent('168 of 300 characters. Saving. The checks run again before you can copy.');
    expect(screen.getByRole('button', { name: 'Copy' })).toHaveAttribute('aria-disabled', 'true');
    rerender(<NoteBlock {...props} note={{ ...note, chars: 150 }} saving={false} />);
    expect(screen.getByRole('status')).toBe(region);
    expect(region).toHaveTextContent('150 of 300 characters. 2 checks passed.');
    expect(screen.getByRole('button', { name: 'Copy' })).toHaveAttribute('aria-disabled', 'false');
  });

  it('with no checks yet Copy waits, because the checks are unknown', () => {
    const calls = noteBlock({ ...noteOf(plan(), 'Pylon'), checks: [] });
    expect(screen.getByRole('status')).toHaveTextContent('The checks have not run yet. Copy waits for them.');
    const copy = screen.getByRole('button', { name: 'Copy' });
    expect(copy).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(copy);
    expect(calls.copy).not.toHaveBeenCalled();
  });

  it('while he edits, the question about his line steps aside', () => {
    noteBlock(noteOf(plan(), 'Inngest'), { editing: <textarea aria-label="Note to Jonah Park" defaultValue="draft" /> });
    expect(screen.queryByText('Is this why you are writing to them? Confirm it or write your own.')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Confirm line' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Write my own' })).toBeNull();
    expect(screen.getByRole('textbox', { name: 'Note to Jonah Park' })).toBeInTheDocument();
  });

  it('the editor takes the place of the note text', () => {
    const note = noteOf(plan(), 'Pylon');
    noteBlock(note, { editing: <textarea aria-label="Note to Lena Ortiz" defaultValue="draft" /> });
    expect(screen.getByRole('textbox', { name: 'Note to Lena Ortiz' })).toBeInTheDocument();
    expect(screen.queryByText(note.text)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
  });

  it('while he edits, his line is a field in the editor and is not shown twice', () => {
    const note = noteOf(plan(), 'Pylon');
    noteBlock(note, { editing: <textarea aria-label="Note to Lena Ortiz" defaultValue="draft" /> });
    expect(screen.queryByText('Why them, in your words')).toBeNull();
    expect(screen.queryByText(note.reasonLine)).toBeNull();
  });
});

describe('StepMarks', () => {
  const steps = (request: string | null, apply: string | null): PlanStep[] => [
    { kind: 'request', touchId: request ? 1 : null, at: request },
    { kind: 'apply', touchId: apply ? 2 : null, at: apply },
  ];

  it('StepMarks shows only Requested when there is no role', () => {
    render(<StepMarks steps={[{ kind: 'request', touchId: null, at: null }]} role={false} />);
    const group = screen.getByRole('group', { name: 'Steps' });
    expect(group).toHaveTextContent('Requested');
    expect(group).not.toHaveTextContent('Applied');
  });

  it('shows the time of day once a step is logged, and an empty mark before', () => {
    render(<StepMarks steps={steps('2026-11-10T14:02:00Z', null)} role />);
    const group = screen.getByRole('group', { name: 'Steps' });
    const [requested, applied] = within(group).getAllByText(/Requested|Applied/).map((el) => el.closest('[data-done]'));
    expect(requested).toHaveAttribute('data-done', 'true');
    expect(requested).toHaveTextContent('Requested 9:02');
    expect(applied).toHaveAttribute('data-done', 'false');
    expect(applied).toHaveTextContent('Applied, not logged yet');
  });
});

describe('SourceLinks', () => {
  it('SourceLinks open in a new tab with noopener', () => {
    render(<SourceLinks links={[{ label: 'Pylon blog, Oct 14', url: 'https://pylon.example/blog/usage-billing' }]} />);
    const link = screen.getByRole('link', { name: 'Pylon blog, Oct 14' });
    expect(link).toHaveAttribute('href', 'https://pylon.example/blog/usage-billing');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(screen.getByText('From')).toBeInTheDocument();
  });

  it('an unsafe URL renders its label as plain text with No link', () => {
    render(<SourceLinks links={[{ label: 'Pylon careers page', url: 'data:text/html,hi' }]} />);
    expect(screen.queryByRole('link')).toBeNull();
    expect(screen.getByText('Pylon careers page')).toBeInTheDocument();
    expect(screen.getByText('No link')).toBeInTheDocument();
  });

  it('takes its spacing from the caller', () => {
    const { container } = render(<SourceLinks links={[{ label: 'Pylon blog', url: 'https://pylon.example/blog' }]} className="mt-3" />);
    expect(container.firstElementChild).toHaveClass('mt-3');
    expect(container.firstElementChild).not.toHaveClass('mt-1.5');
  });

  it('renders nothing with no links', () => {
    const { container } = render(<SourceLinks links={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('WinMark', () => {
  it('WinMark is aria-hidden', () => {
    const { container } = render(<WinMark />);
    const svg = container.querySelector('svg');
    expect(svg).toHaveAttribute('aria-hidden', 'true');
    expect(svg?.querySelector('path')).toHaveClass('win-draw');
  });
});

describe('the win motion', () => {
  // Read as text, as tokens.test.ts does: Vitest's CSS pipeline hands back an empty string for an import.
  const css = readFileSync(path.resolve(import.meta.dirname, '../styles/globals.css'), 'utf8');

  function ruleBody(selector: string): string {
    // A top-level rule starts its line, so `.win-draw` never matches inside `.win-in .win-draw`.
    const start = css.indexOf(`\n${selector} {`);
    if (start < 0) throw new Error(`No ${selector} rule in globals.css`);
    return css.slice(start, css.indexOf('}', start));
  }

  it('globals.css has win-draw and win-in inside the reduced motion override scope', () => {
    expect(css).toMatch(/@keyframes win-draw\s*\{/);
    expect(css).toMatch(/@keyframes win-in\s*\{/);
    const draw = ruleBody('.win-in .win-draw');
    const fade = ruleBody('.win-in');
    expect(draw).toMatch(/animation:\s*win-draw 240ms/);
    expect(fade).toMatch(/animation:\s*win-in 240ms/);
    // The draw runs only inside the banner's entrance, so a remount of the mark alone does not replay it.
    expect(ruleBody('.win-draw')).not.toMatch(/animation/);
    // The override is `* { animation: none !important }`: it beats both rules only while neither is !important.
    expect(draw).not.toContain('!important');
    expect(fade).not.toContain('!important');
    const media = css.indexOf('@media (prefers-reduced-motion: reduce)');
    expect(media).toBeGreaterThan(-1);
    const override = css.slice(media, css.indexOf('}', media));
    expect(override).toMatch(/\*\s*,/);
    expect(override).toContain('animation: none !important');
    // No new token rides along with the motion.
    expect(draw + fade).not.toMatch(/--[a-z]/);
  });
});
