import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture, type FixtureDb, type Scenario } from '@/api/fixture/data';
import { renderWithApp } from '@/test/render';
import { Findings } from './findings';

async function open(o: { scenario?: Scenario; tweak?: (db: FixtureDb) => void } = {}) {
  const db = buildFixture(o.scenario ?? 'default');
  o.tweak?.(db);
  const view = renderWithApp(<Findings />, { client: new FixtureClient(db), route: '/progress' });
  await screen.findByRole('heading', { level: 2, name: 'What the data says' });
  // The heading shows while loading; wait for the skeleton to go.
  await waitFor(() => expect(document.querySelector('[data-slot="skeleton"]')).toBeNull());
  return view;
}

const cards = () => screen.getAllByRole('article');

describe('Findings', () => {
  it('shows each finding with its counts and rates', async () => {
    await open();
    expect(await screen.findByText('October, reported Nov 1')).toBeInTheDocument();
    const [first, second] = cards();
    if (!first || !second) throw new Error('fixture: two findings');
    expect(within(first).getByText('Engineers on the team accepted more often than founders.')).toBeInTheDocument();
    const rows = within(first).getAllByRole('row').map((r) => r.textContent);
    expect(rows).toEqual(['Engineers on the team accepted14 of 33 (42%)', 'Founders accepted5 of 24 (21%)']);
    expect(within(second).getAllByRole('row').map((r) => r.textContent)).toEqual([
      'Replied, role named in the note5 of 71 (7%)',
      'Replied, no role named0 of 29 (0%)',
    ]);
  });

  it('the caution shows for the small group and no percent is drawn for a group under 20', async () => {
    await open({
      tweak: (db) => {
        const f = db.findings.findings[1];
        if (!f) throw new Error('fixture: a second finding');
        f.counts = [{ label: 'Replied, role named in the note', count: 3, of: 12 }, { label: 'Replied, no role named', count: 0, of: 31 }];
      },
    });
    const second = cards()[1];
    if (!second) throw new Error('second finding');
    expect(within(second).getAllByRole('row').map((r) => r.textContent)).toEqual([
      'Replied, role named in the note3 of 12',
      'Replied, no role named0 of 31 (0%)',
    ]);
    expect(within(second).getByText('Only 5 replies in all. Treat this as a hint, not a rule.')).toBeInTheDocument();
  });

  it('a group under 20 never shows a percent, whatever the other group has', async () => {
    await open({
      tweak: (db) => {
        const f = db.findings.findings[0];
        if (!f) throw new Error('fixture');
        f.counts = [{ label: 'A', count: 1, of: 19 }, { label: 'B', count: 1, of: 20 }];
      },
    });
    const first = cards()[0];
    if (!first) throw new Error('first');
    expect(within(first).getAllByRole('row').map((r) => r.textContent)).toEqual(['A1 of 19', 'B1 of 20 (5%)']);
  });

  it('Changed line shows only when set', async () => {
    await open();
    const [first, second] = cards();
    if (!first || !second) throw new Error('fixture: two findings');
    expect(first).toHaveTextContent('Changed: In September founders were ahead. The daily list now puts engineers first.');
    expect(second).not.toHaveTextContent('Changed:');
  });

  it('empty state says when findings arrive', async () => {
    await open({ scenario: 'fresh' });
    expect(screen.getByText('The first findings arrive after your first month of outcomes.')).toBeInTheDocument();
    expect(screen.queryAllByRole('article')).toHaveLength(0);
    expect(screen.queryByText(/reported/)).not.toBeInTheDocument();
  });

  it('a report date with no findings is the same empty state', async () => {
    await open({ tweak: (db) => { db.findings.findings = []; } });
    expect(screen.getByText('The first findings arrive after your first month of outcomes.')).toBeInTheDocument();
  });

  it('earlier changes are collapsed and listed by date', async () => {
    const user = userEvent.setup();
    await open({
      tweak: (db) => {
        db.findings.changes = [
          { at: '2026-10-05T12:00:00Z', text: 'Notes name the role first, from September.' },
          { at: '2026-11-01T12:00:00Z', text: 'The daily list now puts engineers on the team ahead of founders.' },
        ];
      },
    });
    const trigger = screen.getByRole('button', { name: /^Earlier changes/ });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(trigger).toHaveTextContent('2');
    expect(screen.queryByText(/Notes name the role first/)).not.toBeInTheDocument();
    await user.click(trigger);
    expect(screen.getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'Nov 1: The daily list now puts engineers on the team ahead of founders.',
      'Oct 5: Notes name the role first, from September.',
    ]);
  });

  it('shows no earlier changes control when there are none', async () => {
    await open({ tweak: (db) => { db.findings.changes = []; } });
    expect(screen.queryByRole('button', { name: /^Earlier changes/ })).not.toBeInTheDocument();
  });

  it('a finding text is rendered as given (no UI rewording)', async () => {
    await open({
      tweak: (db) => {
        const f = db.findings.findings[0];
        if (!f) throw new Error('fixture');
        f.text = 'Odd <b>words</b>, kept   as given.';
      },
    });
    const first = cards()[0];
    if (!first) throw new Error('first');
    expect(within(first).getByText('Odd <b>words</b>, kept as given.')).toBeInTheDocument();
    expect(first.querySelector('b')).toBeNull();
  });

  it('draws the percent in grey text and never a red tone', async () => {
    const { container } = await open();
    expect(container.querySelector('[class*="text-bad"], [class*="bg-bad"]')).toBeNull();
  });
});
