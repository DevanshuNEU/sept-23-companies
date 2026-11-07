import { screen, waitFor, within } from '@testing-library/react';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture, type FixtureDb } from '@/api/fixture/data';
import { renderWithApp } from '@/test/render';
import PipelinePage from './index';

/*
 * The Pipeline page (2026-11-06): what used to sit under the pick on Today. Last week is covered in last-week.test.tsx and
 * the Watch list in watch-list.test.tsx; this file holds In flight, Email today, the tabs and the empty states, on the
 * `queue` sample (8 picks, 6 more companies to email, 5 in flight).
 */

async function open(route: string, db: FixtureDb = buildFixture('queue')) {
  const client = new FixtureClient(db);
  const view = renderWithApp(<PipelinePage />, { client, route });
  await screen.findByRole('heading', { level: 1, name: 'Pipeline' });
  return { ...view, client, db };
}

describe('the Pipeline page', () => {
  it('has four tabs, each with its count, and says what is waiting in its summary', async () => {
    await open('/pipeline');
    await waitFor(() => expect(screen.getAllByRole('tab').every((t) => /\d$/.test(t.textContent ?? ''))).toBe(true));
    expect(screen.getAllByRole('tab').map((t) => t.textContent?.replace(/\d+$/, '').trim())).toEqual(['Last week', 'In flight', 'Email today', 'Watch list']);
    expect(await screen.findByText(/companies in flight$/)).toBeInTheDocument();
  });

  it('opens the tab in its address', async () => {
    await open('/pipeline?tab=email-today');
    expect(await screen.findByRole('tab', { name: /^Email today/ })).toHaveAttribute('aria-selected', 'true');
    expect(await screen.findByRole('region', { name: 'Email today' })).toBeInTheDocument();
  });

  it('In flight lists each company with where it stands, replies first', async () => {
    await open('/pipeline?tab=in-flight');
    const list = await screen.findByRole('list', { name: 'Companies in flight' });
    expect(within(list).getAllByRole('listitem').map((li) => li.textContent?.replace(/^[A-Z]/, ''))).toEqual([
      'BrowserbaseSomeone replied',
      'Warp4 asked 6 days ago, free again Nov 18',
      'Zed Industries2 asked 5 days ago, room for 2 more, free again Nov 19',
      'Baseten2 asked today, room for 2 more, free again Nov 24',
      'Resend3 asked today, room for 1 more, free again Nov 24',
    ]);
    expect(within(list).getByRole('link', { name: 'Warp' }).getAttribute('href')).toMatch(/^\/companies\/\d+$/);
  });

  it('Email today lists the companies beyond the picks by rank, with a website and no terminal button', async () => {
    const db = buildFixture('queue');
    await open('/pipeline?tab=email-today', db);
    const list = await screen.findByRole('list', { name: 'Companies to email today' });
    const names = within(list).getAllByRole('listitem').map((li) => li.querySelector('a')?.textContent);
    expect(names).toEqual(['Retell AI', 'Railway', 'Graphite', 'Supabase', 'Fly.io', 'Weaviate']);
    const picks = db.plan.rows.map((r) => r.company.name);
    expect(names.filter((n) => n !== undefined && picks.includes(String(n)))).toEqual([]);
    expect(screen.getByText(/^6 more companies you can email today, by rank\./)).toBeInTheDocument();
    expect(within(list).getAllByRole('listitem')[0]).toHaveTextContent('Website known (retellai.example). About 40 people: a founder or engineer reads this');
    expect(within(list).queryByRole('button')).not.toBeInTheDocument();
  });

  it('says plainly when a tab has nothing, in words that say how it fills up', async () => {
    const db = buildFixture('queue');
    db.plan.inFlight = [];
    await open('/pipeline?tab=in-flight', db);
    expect(await screen.findByText('Nothing in flight.')).toBeInTheDocument();
    expect(screen.getByText(/A company you asked in the last 14 days, or one that replied, shows here\./)).toBeInTheDocument();
  });
});
