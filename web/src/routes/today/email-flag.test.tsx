import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient } from '@tanstack/react-query';
import type { EmailFitView } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture } from '@/api/fixture/data';
import { EmailChip } from '@/components/email-chip';
import { TooltipProvider } from '@/components/ui/tooltip';
import { ProfilePanel } from './profile-panel';
import { renderWithApp } from '@/test/render';
import TodayPage from './index';

/*
 * The email flag (2026-10-27) on the `queue` sample: 6 of the 8 picks can be emailed (Cerebrium has no website on file,
 * Hex already had 2 people emailed), and the Email today lane holds 6 more companies by rank (Temporal, 300 people, is
 * not one). Every word is the API's; the page only lays it out.
 */

const EMAIL: EmailFitView = {
  verdict: 'email', label: 'Email', reasons: ['Website known (acme.com)', '11 to 50 people: a founder or engineer reads this'],
  website: { text: 'acme.com', note: 'estimate, your old dashboard' },
};
const LINKEDIN: EmailFitView = {
  verdict: 'linkedin_only', label: 'LinkedIn only', reasons: ['No website on file, so no address pattern'], website: { text: null, note: null },
};

async function open() {
  const client = new FixtureClient(buildFixture('queue'));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  renderWithApp(<TodayPage />, { route: '/today', client, queryClient });
  await screen.findByRole('heading', { level: 2, name: 'Pylon' });
}

const options = () => within(screen.getByRole('listbox', { name: "Today's picks" })).getAllByRole('option');
const panel = () => screen.getByRole('region', { name: 'Selected company' });

describe('EmailChip', () => {
  it('says Email with the reasons on hover, and LinkedIn only in muted text', () => {
    const { rerender } = render(<EmailChip fit={EMAIL} />);
    const chip = screen.getByText('Email');
    expect(chip).toHaveAttribute('data-email-fit', 'email');
    expect(chip).toHaveAttribute('title', 'Email: Website known (acme.com). 11 to 50 people: a founder or engineer reads this.');
    rerender(<EmailChip fit={LINKEDIN} />);
    const muted = screen.getByText('LinkedIn only');
    expect(muted).toHaveClass('text-muted-foreground');
    expect(muted).toHaveAttribute('title', 'LinkedIn only: No website on file, so no address pattern.');
  });

  it('is a button with the reasons when interactive, so a keyboard reaches them', async () => {
    render(<TooltipProvider><EmailChip fit={EMAIL} interactive /></TooltipProvider>);
    const button = screen.getByRole('button', { name: 'Email: Website known (acme.com). 11 to 50 people: a founder or engineer reads this.' });
    await userEvent.setup().hover(button);
    expect((await screen.findAllByText('Website known (acme.com)')).length).toBeGreaterThan(0);
  });
});

describe('Today with the email flag', () => {
  it('counts the picks that can be emailed in the header', async () => {
    await open();
    expect(screen.getByText("6 of today's 8 can be emailed")).toBeInTheDocument();
  });

  it('flags each rail row and keeps its name', async () => {
    await open();
    const [pylon, cerebrium] = options();
    expect(pylon).toHaveAccessibleName('1. Pylon, To do, new opening, 6 hours ago');
    expect(pylon).toHaveAccessibleDescription('Email');
    expect(cerebrium).toHaveAccessibleDescription('LinkedIn only');
    // Only Email shows on the rail; LinkedIn only is for a screen reader there, and the panel says it.
    expect(options().map((o) => o.querySelector('[data-email-fit]')?.getAttribute('data-email-fit') ?? null)).toEqual(
      ['email', null, 'email', 'email', 'email', 'email', 'email', null],
    );
  });

  it('shows the chip and the reasons on the panel', async () => {
    await open();
    expect(within(panel()).getByRole('button', { name: /^Email: Website known \(pylon\.example\)/ })).toBeInTheDocument();
    expect(within(panel()).getByText('Email. Website known (pylon.example). About 30 people: a founder or engineer reads this.')).toBeInTheDocument();
  });

  it('shows the website in the profile, or that none is on file', () => {
    const rows = buildFixture('queue').plan.rows;
    const { unmount } = render(<ProfilePanel row={rows.find((r) => r.company.name === 'Pylon')!} />);
    const site = document.querySelector('[data-fact="website"]');
    expect(site).toHaveTextContent('pylon.example');
    expect(site).toHaveTextContent('estimate, your old dashboard');
    unmount();
    render(<ProfilePanel row={rows.find((r) => r.company.name === 'Cerebrium')!} />);
    expect(document.querySelector('[data-fact="website"]')).toHaveTextContent('Not on file');
  });

  it('says why a pick is LinkedIn only', async () => {
    await open();
    await userEvent.setup().click(options()[1]!);
    await screen.findByRole('heading', { level: 2, name: 'Cerebrium' });
    expect(within(panel()).getByText('LinkedIn only. No website on file, so no address pattern.')).toBeInTheDocument();
  });

  it('lists Email today collapsed, by rank, none of them on today\'s list', async () => {
    const user = userEvent.setup();
    await open();
    const toggle = await screen.findByRole('button', { name: /Email today/ });
    expect(toggle).toHaveTextContent('Email today6 companies');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await user.click(toggle);
    const list = screen.getByRole('list', { name: 'Companies to email today' });
    const names = within(list).getAllByRole('listitem').map((li) => li.querySelector('a')?.textContent);
    expect(names).toEqual(['Retell AI', 'Railway', 'Graphite', 'Supabase', 'Fly.io', 'Weaviate']);
    const picks = options().map((o) => o.querySelector('b')?.textContent);
    expect(names.filter((n) => picks.includes(n))).toEqual([]);
    expect(screen.getByText(/^6 more companies you can email today, by rank\./)).toBeInTheDocument();
    expect(within(list).getAllByRole('listitem')[0]).toHaveTextContent('Website known (retellai.example). About 40 people: a founder or engineer reads this');
  });

  it('copies a lane company\'s line for the terminal', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn<(text: string) => Promise<void>>(async () => {});
    await open();
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    await user.click(await screen.findByRole('button', { name: /Email today/ }));
    await user.click(screen.getByRole('button', { name: 'Copy the line for Retell AI' }));
    expect(writeText).toHaveBeenCalledWith(expect.stringMatching(/^Knock: Retell AI \(company \d+\), role: Software Engineer, New Grad$/));
  });
});
