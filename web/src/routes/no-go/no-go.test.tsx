import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture } from '@/api/fixture/data';
import { AppShell } from '@/shell/app-shell';
import { renderWithApp } from '@/test/render';
import NoGoPage from './index';

function Where() {
  const location = useLocation();
  return <p data-testid="where">{location.pathname + location.search}</p>;
}

const press = (key: string) => fireEvent.keyDown(document.body, { key });
const where = () => screen.getByTestId('where').textContent;
const table = () => screen.getByRole('table');
const bodyRows = () => within(table()).getAllByRole('row').slice(1);
const rowOf = (name: string) => {
  const row = within(table()).getByText(name).closest('tr');
  if (!row) throw new Error(`No row for ${name}`);
  return row;
};

async function page(route = '/no-go', client = new FixtureClient()) {
  const view = renderWithApp(
    <>
      <NoGoPage />
      <Where />
    </>,
    { route, client },
  );
  await screen.findByRole('table');
  return view;
}

describe('No-go list', () => {
  it('shows the tabs with their counts and a chip per reason', async () => {
    await page();
    expect(screen.getByRole('heading', { level: 1, name: 'No-go list' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Companies 24' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'People 7' })).toHaveAttribute('aria-selected', 'false');

    const chips = within(screen.getByRole('radiogroup', { name: 'Filter by reason' })).getAllByRole('radio');
    expect(chips.map((c) => c.textContent)).toEqual([
      'All reasons',
      'Does not sponsor 9',
      'Citizenship or clearance 4',
      'Over 300 people 6',
      'Removed by you 5',
    ]);
    expect(chips[0]).toHaveAttribute('aria-checked', 'true');
    expect(bodyRows()).toHaveLength(24);
  });

  it('shows the reason, evidence, source and added day of each company', async () => {
    await page();
    const halberd = rowOf('Halberd Systems');
    expect(within(halberd).getByText('Does not sponsor')).toBeInTheDocument();
    expect(within(halberd).getByText('"We are unable to sponsor visas now or in the future."')).toBeInTheDocument();
    expect(within(halberd).getByRole('link', { name: 'Backend Engineer posting' })).toHaveAttribute(
      'href',
      'https://careers.example/halberdsystems/backendengineer',
    );
    expect(within(halberd).getByText('Rules, Oct 14')).toBeInTheDocument();

    const quill = rowOf('Quillfeather');
    expect(within(quill).getByText('Closed by you, Nov 3')).toBeInTheDocument();

    const bright = rowOf('Brightwater Analytics');
    expect(within(bright).getByText('Removed by you')).toBeInTheDocument();
    expect(within(bright).getByText('Ad tech, not a fit')).toBeInTheDocument();
    expect(within(bright).getByText('You, from Today')).toBeInTheDocument();
    expect(within(bright).getByText('Not a fit for me')).toBeInTheDocument();
    expect(within(bright).getByText('Nov 4')).toBeInTheDocument();

    const ostrander = rowOf('Ostrander Robotics');
    expect(within(ostrander).getByText('Imported')).toBeInTheDocument();
    expect(within(ostrander).getByText('Old dashboard, locked')).toBeInTheDocument();

    expect(screen.getByText(/Postings hidden for asking for a 2027 graduation/)).toBeInTheDocument();
  });

  it('filtering by Does not sponsor leaves 9 rows, and the filter field narrows by name', async () => {
    const user = userEvent.setup();
    await page();
    await user.click(screen.getByRole('radio', { name: 'Does not sponsor 9' }));
    expect(bodyRows()).toHaveLength(9);
    expect(where()).toBe('/no-go?reason=no_sponsorship');
    expect(within(table()).queryByText('Meridian Defense Labs')).not.toBeInTheDocument();

    await user.type(screen.getByRole('textbox', { name: 'Filter' }), 'halberd');
    expect(bodyRows()).toHaveLength(1);
    expect(within(table()).getByText('Halberd Systems')).toBeInTheDocument();
  });

  it('backspace on a no-go row opens the lift dialog, which needs a reason, then calls liftNoGo', async () => {
    const user = userEvent.setup();
    const { client } = await page();
    const halberd = (await client.noGo()).companies.find((r) => r.kind === 'no_go' && r.target.kind === 'company' && r.target.company.name === 'Halberd Systems');
    if (halberd?.kind !== 'no_go') throw new Error('fixture has no Halberd row');
    const lift = vi.spyOn(client, 'liftNoGo');

    press('j');
    expect(rowOf('Halberd Systems')).toHaveAttribute('aria-selected', 'true');
    press('Backspace');

    const dialog = await screen.findByRole('dialog', { name: 'Take Halberd Systems off the no-go list?' });
    expect(within(dialog).getByText(/Taking it off locks your choice/)).toBeInTheDocument();
    const takeOff = within(dialog).getByRole('button', { name: 'Take it off' });
    expect(takeOff).toHaveAttribute('aria-disabled', 'true');
    await user.click(takeOff);
    expect(lift).not.toHaveBeenCalled();

    await user.type(within(dialog).getByRole('textbox', { name: 'Why' }), 'Their recruiter said they now sponsor');
    expect(takeOff).not.toHaveAttribute('aria-disabled', 'true');
    await user.click(takeOff);

    expect(lift).toHaveBeenCalledTimes(1);
    expect(lift).toHaveBeenCalledWith(halberd.noGoId, 'Their recruiter said they now sponsor', { requestId: expect.any(String), keepalive: true });
    expect(await screen.findByText('Halberd Systems is off the no-go list. It can be proposed again.')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(within(table()).queryByText('Halberd Systems')).not.toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Does not sponsor 8' })).toBeInTheDocument();
  });

  it('backspace on a removed row opens Bring back and calls restoreCompany', async () => {
    const user = userEvent.setup();
    const { client } = await page();
    const bright = (await client.noGo()).companies.find((r) => r.kind === 'removed' && r.company.name === 'Brightwater Analytics');
    if (bright?.kind !== 'removed') throw new Error('fixture has no Brightwater row');
    const restore = vi.spyOn(client, 'restoreCompany');
    const lift = vi.spyOn(client, 'liftNoGo');

    await user.click(rowOf('Brightwater Analytics'));
    expect(rowOf('Brightwater Analytics')).toHaveAttribute('aria-selected', 'true');
    press('Backspace');

    const dialog = await screen.findByRole('dialog', { name: 'Bring back Brightwater Analytics?' });
    const bringBack = within(dialog).getByRole('button', { name: 'Bring back' });
    expect(bringBack).toHaveAttribute('aria-disabled', 'true');
    await user.type(within(dialog).getByRole('textbox', { name: 'Why' }), 'Clicked R by mistake');
    await user.click(bringBack);

    expect(restore).toHaveBeenCalledTimes(1);
    expect(restore).toHaveBeenCalledWith(bright.company.id, 'Clicked R by mistake', bright.company.revision, { requestId: expect.any(String), keepalive: true });
    expect(lift).not.toHaveBeenCalled();
    expect(await screen.findByText('Brightwater Analytics is back in the backlog.')).toBeInTheDocument();
    await waitFor(() => expect(within(table()).queryByText('Brightwater Analytics')).not.toBeInTheDocument());
    expect(screen.getByRole('radio', { name: 'Removed by you 4' })).toBeInTheDocument();
  });

  it('the sidebar count drops at once when an entry comes off', async () => {
    const user = userEvent.setup();
    renderWithApp(
      <>
        <AppShell />
        <NoGoPage />
      </>,
      { route: '/no-go' },
    );
    await screen.findByRole('table');
    const sidebar = document.querySelector<HTMLElement>('[data-slot="sidebar"]') ?? document.body;
    const nav = () => within(sidebar).getByRole('link', { name: /^No-go/ });
    await waitFor(() => expect(within(nav()).getByText('31')).toBeInTheDocument());

    press('j');
    press('Backspace');
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByRole('textbox', { name: 'Why' }), 'Now sponsors');
    await user.click(within(dialog).getByRole('button', { name: 'Take it off' }));
    expect(within(nav()).getByText('30')).toBeInTheDocument();
  });

  it('the People tab lists Ruth Okonkwo as Asked to stop', async () => {
    await page('/no-go/people');
    expect(screen.getByRole('tab', { name: 'People 7' })).toHaveAttribute('aria-selected', 'true');
    const ruth = rowOf('Ruth Okonkwo');
    expect(within(ruth).getByText('Asked to stop')).toBeInTheDocument();
    expect(within(ruth).getByText('Staff Engineer, Supabase')).toBeInTheDocument();
    expect(within(ruth).getByText('"Please take me off your list."')).toBeInTheDocument();
    expect(within(ruth).getByText('Reply, Nov 4')).toBeInTheDocument();

    expect(within(rowOf('Tessa Lindgren')).getByText('Said no')).toBeInTheDocument();
    expect(within(rowOf('Elena Petrova')).getByText('Marked by you')).toBeInTheDocument();
    expect(within(rowOf('Victor Hale')).getByText('Flagged by Claude')).toBeInTheDocument();
    expect(within(rowOf('Victor Hale')).getByText('Profile says "no cold outreach, please"')).toBeInTheDocument();
    expect(bodyRows()).toHaveLength(7);
    expect(screen.queryByRole('radiogroup', { name: 'Filter by reason' })).not.toBeInTheDocument();
  });

  it('backspace on a person opens the lift dialog for them', async () => {
    const user = userEvent.setup();
    const { client } = await page('/no-go/people');
    const lift = vi.spyOn(client, 'liftNoGo');
    press('j');
    press('Backspace');
    const dialog = await screen.findByRole('dialog', { name: 'Take Ruth Okonkwo off the no-go list?' });
    await user.type(within(dialog).getByRole('textbox', { name: 'Why' }), 'She wrote back asking to talk');
    await user.click(within(dialog).getByRole('button', { name: 'Take it off' }));
    expect(lift).toHaveBeenCalledTimes(1);
    expect(await screen.findByText('Ruth Okonkwo is off the no-go list.')).toBeInTheDocument();
  });

  it('1 and 2 switch between Companies and People in the URL', async () => {
    await page();
    press('2');
    expect(where()).toBe('/no-go/people');
    expect(await within(table()).findByText('Ruth Okonkwo')).toBeInTheDocument();
    press('1');
    expect(where()).toBe('/no-go');
    expect(await within(table()).findByText('Halberd Systems')).toBeInTheDocument();
  });

  it('o opens the source of the selected row in a new tab', async () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    await page();
    press('j');
    press('o');
    expect(open).toHaveBeenCalledWith('https://careers.example/halberdsystems/backendengineer', '_blank', 'noopener,noreferrer');
    open.mockRestore();
  });

  it('enter opens the company page of the selected company', async () => {
    const { client } = await page();
    const halberd = (await client.noGo()).companies[0];
    if (halberd?.kind !== 'no_go' || halberd.target.kind !== 'company') throw new Error('fixture order changed');
    press('j');
    press('Enter');
    expect(where()).toBe(`/companies/${halberd.target.company.id}`);
  });

  it('typing backspace in the filter field opens nothing', async () => {
    const user = userEvent.setup();
    await page();
    press('j');
    const field = screen.getByRole('textbox', { name: 'Filter' });
    await user.type(field, 'x{Backspace}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('Clear the filter clears the text and the reason together', async () => {
    const user = userEvent.setup();
    renderWithApp(
      <>
        <NoGoPage />
        <Where />
      </>,
      { route: '/no-go?reason=no_sponsorship&q=zzz' },
    );
    expect(await screen.findByText('Nothing matches this filter.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Clear the filter' }));
    expect(where()).toBe('/no-go');
    expect(await screen.findByRole('table')).toBeInTheDocument();
    expect(bodyRows()).toHaveLength(24);
  });

  it('an unknown reason in the URL reads as All reasons', async () => {
    await page('/no-go?reason=bogus');
    expect(screen.getByRole('radio', { name: 'All reasons' })).toHaveAttribute('aria-checked', 'true');
    expect(bodyRows()).toHaveLength(24);
  });

  it('a dialog opened again right after one closed starts empty and submits', async () => {
    const user = userEvent.setup();
    const { client } = await page();
    const lift = vi.spyOn(client, 'liftNoGo');
    press('j');
    press('Backspace');
    let dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByRole('textbox', { name: 'Why' }), 'First reason');
    await user.click(within(dialog).getByRole('button', { name: 'Take it off' }));
    expect(lift).toHaveBeenCalledTimes(1);
    press('j');
    press('Backspace');
    dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('textbox', { name: 'Why' })).toHaveValue('');
    await user.type(within(dialog).getByRole('textbox', { name: 'Why' }), 'Second reason');
    await user.click(within(dialog).getByRole('button', { name: 'Take it off' }));
    expect(lift).toHaveBeenCalledTimes(2);
    expect(lift).toHaveBeenLastCalledWith(expect.any(Number), 'Second reason', { requestId: expect.any(String), keepalive: true });
  });

  it('shows the empty state when nothing is kept out', async () => {
    renderWithApp(<NoGoPage />, { route: '/no-go', client: new FixtureClient(buildFixture('empty')) });
    expect(await screen.findByText('Nothing is on the no-go list.')).toBeInTheDocument();
  });
});

describe('No-go list, ranking hides', () => {
  it('the No-go view lists ranking hides with their reason and the pending-reader wording', async () => {
    await page();
    const hidden = screen.getByRole('region', { name: 'Hidden by the ranking' });
    const items = within(hidden).getAllByRole('listitem');
    expect(items.map((li) => li.textContent)).toEqual([
      'Maplewood InsuranceOver the size limitAbout 640 people, over the 300 limit.',
      'QuillfeatherNo-goOn your no-go list: no sponsorship, from their CTO reply.',
      'Pellucid SoftwareNo sponsorshipThe posting says it does not sponsor. The reader has not read it yet.Hidden, pending reader',
    ]);
    expect(within(hidden).getByRole('link', { name: 'Maplewood Insurance' })).toHaveAttribute('href', expect.stringMatching(/^\/companies\/\d+$/));
    // Only a provisional hide carries the pending-reader words.
    expect(within(hidden).getAllByText('Hidden, pending reader')).toHaveLength(1);
  });

  it('shows no hidden block when the ranker hides nothing', async () => {
    const db = buildFixture('default');
    db.noGo.hiddenCompanies = [];
    await page('/no-go', new FixtureClient(db));
    expect(screen.queryByRole('region', { name: 'Hidden by the ranking' })).not.toBeInTheDocument();
  });

  it('the text filter narrows the hidden list too', async () => {
    await page('/no-go?q=pellucid');
    const hidden = screen.getByRole('region', { name: 'Hidden by the ranking' });
    expect(within(hidden).getAllByRole('listitem')).toHaveLength(1);
  });

  it('hides are not shown on the People tab', async () => {
    await page('/no-go/people');
    expect(screen.queryByRole('region', { name: 'Hidden by the ranking' })).not.toBeInTheDocument();
  });
});

