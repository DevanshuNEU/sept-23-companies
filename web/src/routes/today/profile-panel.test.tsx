import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PlanRow } from '@/api';
import { buildFixture } from '@/api/fixture/data';
import { ProfileFold, ProfilePanel } from './profile-panel';

const rows = buildFixture('queue').plan.rows;
const rowOf = (name: string): PlanRow => {
  const row = rows.find((r) => r.company.name === name);
  if (!row) throw new Error(`No row for ${name}`);
  return row;
};
const fact = (id: string) => document.querySelector(`[data-fact="${id}"]`) as HTMLElement;

describe('the profile panel: a company that is well known', () => {
  beforeEach(() => void render(<ProfilePanel row={rowOf('Pylon')} />));

  it('has a section for each kind of fact, each with its own small header', () => {
    for (const name of ['What they build', 'Stage and funding', 'Headcount', 'Where', 'Visa', 'Open roles at your level', 'People on record', 'Your history', 'Links']) {
      expect(screen.getByRole('heading', { level: 3, name })).toBeInTheDocument();
    }
  });

  it('says what they build, with the judge\'s domains as chips and where the sentence comes from', () => {
    const build = screen.getByRole('region', { name: 'What they build' });
    expect(build).toHaveTextContent('Builds a support platform for B2B teams, with AI agents that answer tickets.');
    expect(within(build).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['AI agents', 'LLM apps']);
    expect(build).toHaveTextContent('Judged Nov 9');
  });

  it('shows each fact with its confidence and source in small muted text', () => {
    expect(fact('stage')).toHaveTextContent('Series A');
    expect(fact('stage')).toHaveTextContent('confirmed, SEC Form D');
    expect(fact('funding')).toHaveTextContent('$17,000,000 raised, Form D filed Jul 8, 2026');
    expect(fact('headcount')).toHaveTextContent('30 people');
    expect(fact('headcount')).toHaveTextContent('estimate, LinkedIn');
    expect(fact('hq')).toHaveTextContent('San Francisco, CA');
    expect(fact('everify')).toHaveTextContent('E-Verify enrolled');
    expect(fact('everify')).toHaveTextContent('checked Oct 3');
    expect(fact('sponsorship')).toHaveTextContent('Says it sponsors');
    expect(fact('lca')).toHaveTextContent('4 software LCA filings in FY2024 and FY2025');
    expect(fact('lca')).toHaveTextContent('DOL disclosure data');
  });

  it('lists the open roles at his level as links with their level, age and place', () => {
    const roles = screen.getByRole('list', { name: 'Open roles at your level' });
    const items = within(roles).getAllByRole('listitem');
    expect(items).toHaveLength(3);
    expect(within(items[0]!).getByRole('link', { name: 'Software Engineer I' })).toHaveAttribute('href', expect.stringContaining('pylon'));
    expect(items[0]).toHaveTextContent('SWE I role');
    expect(items[0]).toHaveTextContent('first seen 6 hours ago');
    expect(items[1]).toHaveTextContent('Software Engineer II, Platform');
    expect(within(items[1]!).getByRole('link')).toHaveAttribute('target', '_blank');
  });

  it('says how many people are on record and that none were asked yet', () => {
    expect(fact('people')).toHaveTextContent('1 on record, none asked yet');
  });

  it('links to the careers page, the website and the LinkedIn page', () => {
    const links = screen.getByRole('region', { name: 'Links' });
    expect(within(links).getByRole('link', { name: /Careers/ })).toHaveAttribute('href', 'https://pylon.example/careers/swe-1');
    expect(within(links).getByRole('link', { name: /Website/ })).toHaveAttribute('href', expect.stringMatching(/^https:\/\/.*pylon/i));
    expect(within(links).getByRole('link', { name: /LinkedIn page/ })).toHaveAttribute('href', expect.stringContaining('linkedin.com/search/results/companies'));
    for (const a of within(links).getAllByRole('link')) expect(a).toHaveAttribute('rel', expect.stringContaining('noopener'));
  });

  it('reserves a place beside every fact for a future edit pencil, and builds no editing', () => {
    expect(fact('stage').querySelector('[data-slot="edit"]')).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
  });
});

describe('the profile panel: a company with unknown values', () => {
  it('says Not known, muted and never red, where nothing is known', () => {
    render(<ProfilePanel row={rowOf('Trigger.dev')} />);
    for (const id of ['stage', 'hq', 'everify', 'sponsorship', 'lca', 'roleplaces']) {
      expect(fact(id), id).toHaveTextContent('Not known');
      const word = within(fact(id)).getByText('Not known');
      expect(word.className).toContain('text-muted-foreground');
      expect(word.className).not.toMatch(/bad|destructive|red/);
    }
    // The size gate keeps a likely large company off Today, so a headcount nobody looked up is "Not checked".
    expect(fact('headcount')).toHaveTextContent('Not checked');
    expect(fact('headcount')).not.toHaveTextContent('Not known');
    expect(within(fact('headcount')).getByText('Not checked').className).toContain('text-muted-foreground');
    expect(fact('funding')).toHaveTextContent('$5,500,000 raised');
    expect(screen.getByText('Not judged yet')).toBeInTheDocument();
    expect(screen.getByText('None open at your level')).toBeInTheDocument();
    expect(fact('people')).toHaveTextContent('Nobody on record yet');
  });
});

describe('the profile panel: his own history', () => {
  it('says he applied before and that this is a new role', () => {
    render(<ProfilePanel row={rowOf('Hex')} />);
    expect(screen.getByRole('region', { name: 'Your history' })).toHaveTextContent('You applied on Oct 20; this is a new role.');
  });

  it('lists a Not now with its reason', () => {
    render(<ProfilePanel row={rowOf('Inngest')} />);
    const history = screen.getByRole('region', { name: 'Your history' });
    expect(history).toHaveTextContent('Not now on Oct 28: too early');
    expect(history).toHaveTextContent('Request to Jonah Park (Manager) on Oct 21');
  });

  it('says there is nothing yet for a company he has never touched', () => {
    render(<ProfilePanel row={rowOf('Cerebrium')} />);
    expect(screen.getByRole('region', { name: 'Your history' })).toHaveTextContent('Nothing yet');
  });

  it('says when the company is free again for a company he asked today', () => {
    render(<ProfilePanel row={rowOf('Resend')} />);
    expect(fact('people')).toHaveTextContent('4 on record, 3 asked, free again Nov 24');
  });
});

describe('the profile fold, under 1280 px', () => {
  it('is a collapsed Company profile section that opens to the same facts', async () => {
    render(<ProfileFold row={rowOf('Pylon')} />);
    const toggle = screen.getByRole('button', { name: /Company profile/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveTextContent('Series A');
    expect(screen.queryByRole('heading', { level: 3, name: 'Visa' })).toBeNull();
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('heading', { level: 3, name: 'Visa' })).toBeInTheDocument();
    expect(fact('lca')).toHaveTextContent('4 software LCA filings');
  });

  it('summarises a company that is mostly unknown without a word of blame', () => {
    render(<ProfileFold row={rowOf('Trigger.dev')} />);
    expect(screen.getByRole('button', { name: /Company profile/ })).toHaveTextContent('Not much known yet');
  });
});
