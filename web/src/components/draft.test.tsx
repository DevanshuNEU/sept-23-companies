import type { ComponentProps } from 'react';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture } from '@/api/fixture/data';
import type { Check, CheckFix, Fact, OutboxDetail } from '@/api';
import { CheckList } from '@/components/check-list';
import { CloseCompanyDialog } from '@/components/close-company-dialog';
import { DraftEditor } from '@/components/draft-editor';
import { EvidenceList } from '@/components/evidence-list';

/** Kenji Watanabe's draft, blocked as a copy of Ava Moreno's: the body and checks the Outbox mockup shows. */
async function kenji(): Promise<OutboxDetail> {
  const client = new FixtureClient(buildFixture('default'));
  const view = await client.outbox();
  const item = view.items.find((i) => i.person.name === 'Kenji Watanabe');
  if (!item) throw new Error('No Kenji in the fixture');
  return client.outboxDetail(item.touchId);
}

const check = (code: string, severity: Check['severity'], title: string, extra: Partial<Check> = {}): Check => ({
  code, severity, title, detail: '', fix: null, spans: [], ...extra,
});

describe('CheckList', () => {
  it('lists blocks, then warnings, then one collapsed passed line, whatever the input order', () => {
    const checks = [
      check('p1', 'pass', 'No banned phrases'),
      check('w1', 'warn', 'Length is 70 words'),
      check('b1', 'block', 'No mail server for graphtie.example'),
      check('p2', 'pass', 'No em or en dashes'),
    ];
    render(<CheckList checks={checks} onFix={() => {}} />);
    const rows = screen.getAllByRole('listitem');
    expect(rows.map((r) => r.getAttribute('data-severity'))).toEqual(['block', 'warn']);
    expect(rows[0]).toHaveTextContent('No mail server for graphtie.example');
    expect(rows[1]).toHaveTextContent('Length is 70 words');
    expect(screen.getByRole('button', { name: /2 checks passed/ })).toBeInTheDocument();
  });

  it('keeps nine passed checks collapsed until clicked', async () => {
    const detail = await kenji();
    render(<CheckList checks={detail.checks} onFix={() => {}} />);
    const toggle = screen.getByRole('button', { name: /9 checks passed/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('No banned phrases')).not.toBeInTheDocument();
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('No banned phrases')).toBeInTheDocument();
    expect(screen.getByText('No em or en dashes')).toBeInTheDocument();
  });

  it('Compare drafts calls onFix with the other message', async () => {
    const detail = await kenji();
    const onFix = vi.fn<(fix: CheckFix) => void>();
    render(<CheckList checks={detail.checks} onFix={onFix} />);
    await userEvent.click(screen.getByRole('button', { name: 'Compare drafts' }));
    expect(onFix).toHaveBeenCalledTimes(1);
    expect(onFix.mock.calls[0]?.[0]).toEqual({ kind: 'compare', otherMessageId: expect.any(Number) });
  });

  it('gives each fix its own button and shows no select for a role_kind fix without one', async () => {
    const onFix = vi.fn();
    const checks = [
      check('b1', 'block', 'No mail server', { fix: { kind: 'review_item', reviewId: 7 } }),
      check('b2', 'block', 'On the no-go list', { fix: { kind: 'no_go', noGoId: 3 } }),
      check('w1', 'warn', 'Recruiter title', { fix: { kind: 'role_kind' } }),
    ];
    render(<CheckList checks={checks} onFix={onFix} />);
    await userEvent.click(screen.getByRole('button', { name: 'Fix in Needs you' }));
    await userEvent.click(screen.getByRole('button', { name: 'Open no-go entry' }));
    expect(onFix.mock.calls).toEqual([[{ kind: 'review_item', reviewId: 7 }], [{ kind: 'no_go', noGoId: 3 }]]);
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  });

  it('passes the role kind select through for a role_kind fix', async () => {
    const onChange = vi.fn();
    const checks = [check('w1', 'warn', 'Recruiter title', { detail: 'Recruiters go out on day 2.', fix: { kind: 'role_kind' } })];
    render(<CheckList checks={checks} onFix={() => {}} roleKind={{ value: 'recruiter', onChange }} />);
    const select = screen.getByRole('combobox', { name: 'Role kind' });
    expect(select).toHaveTextContent('Recruiter');
    await userEvent.click(select);
    await userEvent.click(screen.getByRole('option', { name: 'Engineer' }));
    expect(onChange).toHaveBeenCalledWith('engineer');
  });
});

describe('DraftEditor', () => {
  const base: Pick<ComponentProps<typeof DraftEditor>, 'spans' | 'savedBy' | 'savedAt' | 'kind' | 'seq' | 'dirty'> = {
    spans: [], savedBy: 'claude', savedAt: '2026-11-10T14:58:00Z', kind: 'engineer', seq: 0, dirty: false,
  };

  it('sets the body in the serif note face, on a card', async () => {
    const detail = await kenji();
    render(<DraftEditor {...base} subject={detail.item.subject} body={detail.body} onChange={() => {}} />);
    const body = screen.getByLabelText('Email body');
    expect(body).toHaveClass('font-serif', 'text-[16px]', 'leading-[1.65]', 'max-w-[62ch]');
    // The card is the editor's own box, so the flag marks behind the text stay visible.
    expect(body.closest('.bg-card')).not.toBeNull();
  });

  it('shows the Kenji meter: 91 words, 2 links, plain text and who saved when', async () => {
    const detail = await kenji();
    render(<DraftEditor {...base} subject={detail.item.subject} body={detail.body} onChange={() => {}} />);
    expect(screen.getByText('91 words')).toBeInTheDocument();
    expect(screen.getByText('2 links')).toBeInTheDocument();
    expect(screen.getByText('Plain text')).toBeInTheDocument();
    expect(screen.getByText('Saved by Claude 09:58')).toBeInTheDocument();
    expect(screen.getByLabelText('Subject')).toHaveValue('your post on safe task retries');
    expect(screen.getByLabelText('Email body')).toHaveValue(detail.body);
  });

  it('does not count URLs as words, and does not count an email address as a link', () => {
    const body =
      'Hi Kenji,\n\nThanks for reading, reach me at me@mail.example.\n\nhttps://www.devanshuchicholikar.com/a/b?c=1\nhttps://opencodeintel.com';
    render(<DraftEditor {...base} subject="s" body={body} onChange={() => {}} />);
    expect(screen.getByText('9 words')).toBeInTheDocument();
    expect(screen.getByText('2 links')).toBeInTheDocument();
  });

  it('counts a bare domain as a link, as the checker does, and says 1 link', () => {
    render(<DraftEditor {...base} subject="s" body="see devanshuchicholikar.com" onChange={() => {}} />);
    expect(screen.getByText('1 link')).toBeInTheDocument();
    expect(screen.getByText('2 words')).toBeInTheDocument();
  });

  it('says who saved last: you, with the app-zone time', () => {
    render(<DraftEditor {...base} savedBy="devanshu" savedAt="2026-11-10T15:03:00Z" subject="s" body="b" onChange={() => {}} />);
    expect(screen.getByText('Saved by you 10:03')).toBeInTheDocument();
    expect(screen.queryByText('Unsaved changes')).not.toBeInTheDocument();
  });

  it('flags unsaved edits', () => {
    render(<DraftEditor {...base} dirty subject="s" body="b" onChange={() => {}} />);
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument();
  });

  it('marks a flagged span in a layer behind the plain textarea', () => {
    const body = "Hi Kenji,\n\nI'd love to work on this. Could you refer me?";
    const start = body.indexOf("I'd love to");
    const { container } = render(
      <DraftEditor {...base} subject="s" body={body} spans={[{ field: 'body', start, end: start + "I'd love to".length }]} onChange={() => {}} />,
    );
    const marks = container.querySelectorAll('mark');
    expect(marks).toHaveLength(1);
    expect(marks[0]).toHaveTextContent("I'd love to");
    expect(marks[0]?.closest('[aria-hidden="true"]')).not.toBeNull();
    // The textarea itself stays plain text.
    expect(screen.getByLabelText('Email body')).toHaveValue(body);
    expect(screen.getByLabelText('Email body').querySelector('mark')).toBeNull();
  });

  it('indexes a span into the subject or the body by its field, clamps, and merges overlaps', () => {
    const { container } = render(
      <DraftEditor
        {...base}
        subject="so excited to talk"
        body="abcdefghij"
        spans={[
          { field: 'subject', start: 3, end: 10 },
          { field: 'body', start: 2, end: 5 },
          { field: 'body', start: 4, end: 7 },
          { field: 'body', start: 8, end: 99 },
        ]}
        onChange={() => {}}
      />,
    );
    expect([...container.querySelectorAll('mark')].map((m) => m.textContent)).toEqual(['excited', 'cdefg', 'ij']);
  });

  it('reports a typed change with the other field kept', async () => {
    const onChange = vi.fn();
    render(<DraftEditor {...base} subject="s" body="b" onChange={onChange} />);
    await userEvent.type(screen.getByLabelText('Subject'), 'x');
    expect(onChange).toHaveBeenLastCalledWith({ subject: 'sx', body: 'b' });
    await userEvent.type(screen.getByLabelText('Email body'), 'y');
    expect(onChange).toHaveBeenLastCalledWith({ subject: 's', body: 'by' });
  });
});

describe('EvidenceList', () => {
  const fact = (o: Partial<Fact>): Fact => ({
    id: 1, fact: 'size', value: '48', low: null, high: null, confidence: 'confirmed', locked: false,
    sourceKind: 'linkedin', sourceRef: 'LinkedIn and careers page', quote: '51 to 200 employees, 48 listed', observedOn: null, ...o,
  });

  it('shows the fact, value, source, quote, confidence and a lock', () => {
    render(
      <EvidenceList
        facts={[
          fact({}),
          fact({ id: 2, fact: 'domain', value: 'browserbase.example', sourceKind: 'hand', sourceRef: '', quote: '', locked: true, observedOn: '2026-10-30' }),
          fact({ id: 3, fact: 'stage', value: 'Series B', confidence: 'estimate', sourceKind: 'website', sourceRef: 'Company blog', quote: 'announced its Series B' }),
        ]}
      />,
    );
    const rows = screen.getAllByRole('row').slice(1);
    expect(rows).toHaveLength(3);
    const [size, domain, stage] = rows as [HTMLElement, HTMLElement, HTMLElement];
    expect(within(size).getByText('Size')).toBeInTheDocument();
    expect(within(size).getByText('LinkedIn and careers page')).toBeInTheDocument();
    expect(within(size).getByRole('img', { name: 'High confidence' })).toBeInTheDocument();
    expect(within(size).queryByText('Locked')).not.toBeInTheDocument();
    expect(within(domain).getByText('Email domain')).toBeInTheDocument();
    expect(within(domain).getByText('You')).toBeInTheDocument();
    expect(within(domain).getByText(', Fri Oct 30')).toBeInTheDocument();
    expect(within(domain).getByText('Locked')).toBeInTheDocument();
    expect(within(stage).getByRole('img', { name: 'Medium confidence' })).toBeInTheDocument();
    expect(within(stage).getByText('"announced its Series B"')).toBeInTheDocument();
  });

  it('links a source that is a URL and says so when there are no facts', () => {
    const { rerender } = render(<EvidenceList facts={[fact({ sourceKind: 'posting', sourceRef: 'https://jobs.example/1' })]} />);
    expect(screen.getByRole('link', { name: 'Posting' })).toHaveAttribute('href', 'https://jobs.example/1');
    rerender(<EvidenceList facts={[]} />);
    expect(screen.getByText('No evidence yet for this company.')).toBeInTheDocument();
  });
});

describe('CloseCompanyDialog', () => {
  const company = {
    id: 1, name: 'Browserbase', status: 'working', pausedAt: null, heldByTouchId: null, held: false, heldByReply: null,
  } as const;
  const SEVEN = ['Referral', 'Interview', 'No sponsorship', 'No referrals', 'Already hired', 'Not now', 'Other'];

  it('lists the seven reasons, with the no-go hint on No sponsorship', () => {
    render(<CloseCompanyDialog company={company} open onOpenChange={() => {}} onClose={() => {}} />);
    expect(screen.getByRole('dialog', { name: 'Close Browserbase' })).toBeInTheDocument();
    const radios = screen.getAllByRole('radio');
    expect(radios).toHaveLength(7);
    expect(radios.map((r) => r.id.replace('close-reason-', ''))).toEqual([
      'referral', 'interview', 'no_sponsorship', 'no_referrals', 'already_hired', 'not_now', 'other',
    ]);
    for (const name of SEVEN) expect(screen.getByRole('radio', { name: new RegExp(`^${name}`) })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'No sponsorship (adds it to the no-go list)' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Interview' })).toBeChecked();
  });

  it("calls onClose('interview', '') and closes", async () => {
    const onClose = vi.fn();
    const onOpenChange = vi.fn();
    render(<CloseCompanyDialog company={company} open onOpenChange={onOpenChange} onClose={onClose} />);
    await userEvent.click(screen.getByRole('button', { name: 'Close company' }));
    expect(onClose).toHaveBeenCalledExactlyOnceWith('interview', '');
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('passes the chosen reason and the note, and Enter in the note submits', async () => {
    const onClose = vi.fn();
    render(<CloseCompanyDialog company={company} open onOpenChange={() => {}} onClose={onClose} />);
    await userEvent.click(screen.getByRole('radio', { name: /^No sponsorship/ }));
    await userEvent.type(screen.getByLabelText('Note'), 'Posting says no visas{Enter}');
    expect(onClose).toHaveBeenCalledExactlyOnceWith('no_sponsorship', 'Posting says no visas');
  });

  it('a double click on Close company closes once', async () => {
    const onClose = vi.fn();
    render(<CloseCompanyDialog company={company} open onOpenChange={() => {}} onClose={onClose} />);
    await userEvent.dblClick(screen.getByRole('button', { name: 'Close company' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Enter pressed twice in the note closes once', async () => {
    const onClose = vi.fn();
    render(<CloseCompanyDialog company={company} open onOpenChange={() => {}} onClose={onClose} />);
    await userEvent.type(screen.getByLabelText('Note'), 'done{Enter}{Enter}');
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledWith('interview', 'done');
  });

  it('Cancel closes without calling onClose, and renders nothing while closed', async () => {
    const onClose = vi.fn();
    const onOpenChange = vi.fn();
    const { rerender } = render(<CloseCompanyDialog company={company} open onOpenChange={onOpenChange} onClose={onClose} />);
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(onClose).not.toHaveBeenCalled();
    rerender(<CloseCompanyDialog company={company} open={false} onOpenChange={onOpenChange} onClose={onClose} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('starts over each time it opens', async () => {
    const props = { company, onOpenChange: () => {}, onClose: () => {} };
    const { rerender } = render(<CloseCompanyDialog {...props} open />);
    await userEvent.click(screen.getByRole('radio', { name: 'Other' }));
    await userEvent.type(screen.getByLabelText('Note'), 'x');
    rerender(<CloseCompanyDialog {...props} open={false} />);
    rerender(<CloseCompanyDialog {...props} open />);
    expect(screen.getByRole('radio', { name: 'Interview' })).toBeChecked();
    expect(screen.getByLabelText('Note')).toHaveValue('');
  });
});
