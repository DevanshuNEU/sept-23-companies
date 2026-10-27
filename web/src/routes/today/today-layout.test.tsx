import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient } from '@tanstack/react-query';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture } from '@/api/fixture/data';
import { monogramHue } from '@/lib/monogram';
import { renderWithApp } from '@/test/render';
import TodayPage from './index';

/*
 * The Today redesign, on the `queue` sample: three columns from 1280 px (rail, main, profile), the profile folding under
 * the main column below it, the calmer main column (header, chips, one filled action, closed disclosures) and the
 * livelier rail (monograms, fit chips, the day's progress).
 */

const realMatchMedia = window.matchMedia;
/** A window `wide` or not: only the 1280 px query answers yes. */
function setWindow(wide: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: wide && query === '(min-width: 1280px)',
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

const press = (key: string) => fireEvent.keyDown(document.body, { key });
const options = () => within(screen.getByRole('listbox', { name: "Today's picks" })).getAllByRole('option');
const main = () => screen.getByRole('region', { name: 'Selected company' });
const layout = () => document.querySelector('[data-layout]') as HTMLElement;

async function open(db = buildFixture('queue')) {
  const client = new FixtureClient(db);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  renderWithApp(<TodayPage />, { route: '/today', client, queryClient });
  await screen.findByRole('heading', { level: 2, name: 'Pylon' });
}

afterEach(() => {
  window.matchMedia = realMatchMedia;
});

describe('three columns from 1280 px', () => {
  beforeEach(() => setWindow(true));

  it('puts the rail, the company and its profile side by side, in that order', async () => {
    await open();
    expect(layout()).toHaveAttribute('data-layout', 'three-columns');
    const rail = screen.getByRole('listbox', { name: "Today's picks" });
    const profile = screen.getByRole('complementary', { name: 'Company profile' });
    expect(rail.compareDocumentPosition(main()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(main().compareDocumentPosition(profile) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // The profile is a column of its own: it is not inside the main column, and nothing folds.
    expect(main().contains(profile)).toBe(false);
    expect(screen.queryByRole('button', { name: /Company profile/ })).toBeNull();
    expect(layout().className).toMatch(/grid-cols-\[clamp\(256px,19vw,292px\)_minmax\(0,1fr\)_clamp\(296px,22vw,350px\)\]/);
  });

  it('shows the selected company\'s profile and follows the selection', async () => {
    await open();
    const profile = screen.getByRole('complementary', { name: 'Company profile' });
    expect(profile).toHaveTextContent('Builds a support platform for B2B teams');
    expect(profile).toHaveTextContent('30 people');
    fireEvent.click(options()[2]!); // Resend
    await screen.findByRole('heading', { level: 2, name: 'Resend' });
    expect(profile).toHaveTextContent('Builds an email API for developers.');
    expect(profile).toHaveTextContent('38 people');
    expect(profile).not.toHaveTextContent('Builds a support platform');
  });

  it('says Not known for what is unknown, and nothing in it is red', async () => {
    await open();
    fireEvent.click(options()[6]!); // Trigger.dev
    await screen.findByRole('heading', { level: 2, name: 'Trigger.dev' });
    const profile = screen.getByRole('complementary', { name: 'Company profile' });
    expect(within(profile).getAllByText('Not known').length).toBeGreaterThanOrEqual(6);
    expect(profile.innerHTML).not.toMatch(/\b(?:text|bg|border)-(?:bad|destructive)|\bred\b/);
  });

  it('keeps the profile when a key acts on the row (the page still answers X and its keys)', async () => {
    await open();
    press('j');
    await screen.findByRole('heading', { level: 2, name: 'Cerebrium' });
    expect(screen.getByRole('complementary', { name: 'Company profile' })).toHaveTextContent('Builds serverless GPU infrastructure for AI apps.');
  });
});

describe('under 1280 px the profile folds under the company', () => {
  beforeEach(() => setWindow(false));

  it('is a collapsed Company profile section inside the main column, with nothing lost', async () => {
    const user = userEvent.setup();
    await open();
    expect(layout()).toHaveAttribute('data-layout', 'two-columns');
    expect(screen.queryByRole('complementary', { name: 'Company profile' })).toBeNull();
    const toggle = within(main()).getByRole('button', { name: /Company profile/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveTextContent('Series A, 30 people, San Francisco, CA');
    expect(within(main()).queryByRole('heading', { level: 3, name: 'Visa' })).toBeNull();
    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    for (const h of ['What they build', 'Stage and funding', 'Headcount', 'Where', 'Visa', 'Open roles at your level', 'People on record', 'Your history', 'Links']) {
      expect(within(main()).getByRole('heading', { level: 3, name: h })).toBeInTheDocument();
    }
    expect(main()).toHaveTextContent('4 software LCA filings in FY2024 and FY2025');
  });

  it('closes again when he moves to another company', async () => {
    const user = userEvent.setup();
    await open();
    await user.click(within(main()).getByRole('button', { name: /Company profile/ }));
    fireEvent.click(options()[1]!);
    await screen.findByRole('heading', { level: 2, name: 'Cerebrium' });
    expect(within(main()).getByRole('button', { name: /Company profile/ })).toHaveAttribute('aria-expanded', 'false');
  });
});

describe('the company, calmer', () => {
  beforeEach(() => setWindow(true));

  it('leads with the name, what they build, the level chip and the fit chip', async () => {
    await open();
    expect(screen.getByRole('heading', { level: 2, name: 'Pylon' }).className).toMatch(/text-\[28px\].*tracking-\[-0\.025em\]/);
    expect(main()).toHaveTextContent('Builds a support platform for B2B teams, with AI agents that answer tickets.');
    expect(within(main()).getByText('SWE I role')).toBeInTheDocument();
    expect(within(main()).getByText('Fit 82')).toHaveAttribute('data-band', 'good');
  });

  it('colours the fit by its band, and says nothing of fit for a company the judge has not read', async () => {
    await open();
    fireEvent.click(options()[2]!); // Resend: 64
    await screen.findByRole('heading', { level: 2, name: 'Resend' });
    expect(within(main()).getByText('Fit 64')).toHaveAttribute('data-band', 'mid');
    fireEvent.click(options()[4]!); // Inngest: not judged
    await screen.findByRole('heading', { level: 2, name: 'Inngest' });
    expect(within(main()).queryByText(/^Fit \d+/)).toBeNull();
    expect(main()).toHaveTextContent('What they build is not known yet.');
  });

  it('has one filled action, Open LinkedIn search, with the others quieter', async () => {
    await open();
    const filled = Array.from(main().querySelectorAll('[data-slot="button"][data-variant="default"]'));
    expect(filled.map((e) => e.textContent)).toEqual(['Open LinkedIn searchS']);
    for (const name of ['Careers', 'Website', 'LinkedIn page', 'Copy for terminal', 'Log applied']) {
      const quiet = within(main()).getByRole(name === 'Copy for terminal' || name === 'Log applied' ? 'button' : 'link', { name: new RegExp(`^${name}`) });
      expect(quiet).toHaveAttribute('data-variant', 'secondary');
    }
  });

  it('keeps Log people you asked and Save this search closed, and Carry and Not now at the bottom', async () => {
    const user = userEvent.setup();
    await open();
    const people = within(main()).getByRole('button', { name: /Log people you asked/ });
    const save = within(main()).getByRole('button', { name: /Save this search/ });
    for (const b of [people, save]) expect(b).toHaveAttribute('aria-expanded', 'false');
    expect(within(main()).queryByRole('textbox')).toBeNull();
    const carry = within(main()).getByRole('button', { name: /Carry to tomorrow/ });
    const notNow = within(main()).getByRole('button', { name: /Not now/ });
    for (const b of [people, save]) {
      expect(b.compareDocumentPosition(carry) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(b.compareDocumentPosition(notNow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    await user.click(people);
    expect(people).toHaveAttribute('aria-expanded', 'true');
    expect(within(main()).getByRole('textbox', { name: /Log people you asked/ })).toBeInTheDocument();
    await user.click(save);
    expect(within(main()).getByRole('textbox', { name: 'Save this search' })).toBeInTheDocument();
  });

  it('L opens the Log people box and puts the cursor in it', async () => {
    await open();
    expect(within(main()).queryByRole('textbox')).toBeNull();
    (document.activeElement as HTMLElement | null)?.blur();
    press('l');
    const box = await within(main()).findByRole('textbox', { name: /Log people you asked/ });
    await waitFor(() => expect(box).toHaveFocus());
    expect(within(main()).getByRole('button', { name: /Log people you asked/ })).toHaveAttribute('aria-expanded', 'true');
  });

  it('closes the Log people box again on another company', async () => {
    await open();
    press('l');
    await within(main()).findByRole('textbox', { name: /Log people you asked/ });
    fireEvent.click(options()[1]!);
    await screen.findByRole('heading', { level: 2, name: 'Cerebrium' });
    expect(within(main()).queryByRole('textbox')).toBeNull();
  });

  it('a company whose wave is full has no Log people box, and L says so', async () => {
    const db = buildFixture('queue');
    const baseten = db.plan.rows.find((r) => r.company.name === 'Baseten');
    if (!baseten?.queue) throw new Error('fixture: Baseten');
    baseten.queue.standing = { ...baseten.queue.standing, asked: 4, room: 0 };
    await open(db);
    fireEvent.click(options()[3]!);
    await screen.findByRole('heading', { level: 2, name: 'Baseten' });
    expect(within(main()).queryByRole('button', { name: /Log people you asked/ })).toBeNull();
    (document.activeElement as HTMLElement | null)?.blur();
    press('l');
    expect(await screen.findByText('Nothing to log at Baseten: its wave is full.')).toBeInTheDocument();
  });
});

describe('the rail, more alive', () => {
  beforeEach(() => setWindow(true));

  it('gives every company a monogram tile in a hue its name always gets', async () => {
    await open();
    const tiles = options().map((o) => o.querySelector('[data-hue]'));
    expect(tiles.map((t) => t?.textContent)).toEqual(['P', 'C', 'R', 'B', 'I', 'T', 'T', 'H']);
    expect(tiles.map((t) => t?.getAttribute('data-hue'))).toEqual(
      ['Pylon', 'Cerebrium', 'Resend', 'Baseten', 'Inngest', 'Turso', 'Trigger.dev', 'Hex'].map(monogramHue),
    );
  });

  it('has the fit chip on the right of a judged company, and none on one the judge has not read', async () => {
    await open();
    expect(options()[0]!.querySelector('[data-judge]')).toHaveTextContent('Fit 82');
    expect(options()[2]!.querySelector('[data-judge]')).toHaveAttribute('data-band', 'mid');
    expect(options()[4]!.querySelector('[data-judge]')).toBeNull();
  });

  it('shows the day as a slim bar, 2 of 8 reached, and marks the selected row', async () => {
    await open();
    const bar = screen.getByRole('meter', { name: 'Picks reached today' });
    expect(bar).toHaveAttribute('aria-valuenow', '2');
    expect(bar).toHaveAttribute('aria-valuemax', '8');
    expect(bar).toHaveAttribute('aria-valuetext', '2 of 8 reached');
    expect(screen.getByText('2 of 8 reached')).toBeVisible();
    expect(options()[0]).toHaveAttribute('aria-selected', 'true');
    expect(options()[0]!.className).toContain('aria-selected:bg-card');
  });

  it('marks a company he reached with a check, and says its word', async () => {
    await open();
    expect(options()[2]).toHaveAccessibleName('3. Resend, Done');
    expect(options()[2]).toHaveTextContent('Done');
    expect(options()[2]).toHaveAttribute('data-node', 'done');
  });
});
