import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient } from '@tanstack/react-query';
import { useLocation } from 'react-router';
import { toast } from 'sonner';
import { queryKeys, type LinkedInView } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture } from '@/api/fixture/data';
import { renderWithApp } from '@/test/render';
import LinkedInPage from './index';

const press = (key: string, init: KeyboardEventInit = {}) => fireEvent.keyDown(document.body, { key, ...init });
// The live region also says the selected name, so rows are looked up inside the table (hidden while a dialog is open).
const table = () => screen.getByRole('table', { hidden: true });
const row = (name: string) => {
  const tr = within(table()).getByText(name, { exact: true }).closest('tr');
  if (!tr) throw new Error(`no row for ${name}`);
  return tr;
};
const queryRow = (name: string) => within(table()).queryByText(name, { exact: true })?.closest('tr') ?? null;
const pagehide = () => fireEvent(window, new Event('pagehide'));

async function setup(client = new FixtureClient(), route = '/linkedin') {
  const view = renderWithApp(<LinkedInPage />, { route, client });
  await screen.findByText(route.includes('all=1') ? 'Hannah Cole' : 'Anneke Visser');
  const spy = vi.spyOn(client, 'logLinkedIn');
  const data = await client.linkedin();
  const idOf = (name: string) => {
    const p = data.groups.flatMap((g) => g.people).find((x) => x.person.name === name);
    if (!p) throw new Error(`no person ${name}`);
    return p.person.id;
  };
  return { ...view, client, spy, idOf };
}

/** The list with All recorded on: everyone, as the old All tab showed them. */
const setupAll = (client = new FixtureClient()) => setup(client, '/linkedin?all=1');
const allToggle = () => screen.getByRole('button', { name: /^All recorded/ });
const markBox = () => screen.getByRole('textbox', { name: 'Mark someone as replied' });
const picks = () => within(screen.getByRole('list', { name: 'People you messaged' })).queryAllByRole('button');

afterEach(() => {
  toast.dismiss();
});

describe('LinkedIn replies', () => {
  it('lists only the people who replied, with the counts line and no tabs', async () => {
    await setup();
    expect(screen.getByRole('heading', { level: 1, name: 'LinkedIn replies' })).toBeInTheDocument();
    expect(row('Anneke Visser')).toHaveTextContent('Replied');
    expect(row('Mira Shah')).toBeInTheDocument();
    expect(queryRow('Hannah Cole')).toBeNull();
    expect(queryRow('Luis Romero')).toBeNull();
    expect(screen.queryAllByRole('tab')).toHaveLength(0);
    // Counts only, no percent: requests 2 to 6 days ago are in the 7 days, older ones only in the total.
    expect(screen.getByText(/^Last 7 days:/)).toHaveTextContent(
      'Last 7 days: 8 people messaged, 2 replied. In total: 11 people messaged, 2 replied.',
    );
    expect(screen.queryByText(/%/)).toBeNull();
    expect(allToggle()).toHaveAttribute('aria-pressed', 'false');
    expect(allToggle()).toHaveTextContent('31');
    expect(screen.getByText('Showing 1 to 2 of 2 people')).toBeInTheDocument();
  });

  it('All recorded shows everyone, and v turns it off again', async () => {
    const user = userEvent.setup();
    await setup();
    await user.click(allToggle());
    await waitFor(() => expect(queryRow('Hannah Cole')).not.toBeNull());
    expect(allToggle()).toHaveAttribute('aria-pressed', 'true');
    expect(row('Luis Romero')).toBeInTheDocument();
    expect(row('Mira Shah')).toBeInTheDocument();
    press('v');
    await waitFor(() => expect(queryRow('Hannah Cole')).toBeNull());
    expect(row('Mira Shah')).toBeInTheDocument();
    press('v');
    await waitFor(() => expect(queryRow('Hannah Cole')).not.toBeNull());
  });

  it('shows the Mintlify header with the role and "8 emailed, 4 here"', async () => {
    await setupAll();
    const header = screen.getByRole('link', { name: 'Mintlify' }).closest('tr');
    if (!header) throw new Error('no Mintlify header');
    expect(header).toHaveTextContent('Role to name');
    expect(header).toHaveTextContent('Software Engineer, New Grad');
    expect(header).toHaveTextContent('8 emailed, 4 here');
  });

  it('shows why LinkedIn, the kind and the next step on each row', async () => {
    await setupAll();
    const hannah = row('Hannah Cole');
    expect(hannah).toHaveTextContent('Engineer');
    expect(hannah).toHaveTextContent('Past the 8 emailed');
    expect(hannah).toHaveTextContent('Not started');
    expect(within(hannah).getByRole('button', { name: /Log request/ })).toBeInTheDocument();
    expect(row('Ethan Wu')).toHaveTextContent('No email found');
  });

  it('points to Today\'s plan in one line', async () => {
    await setup();
    expect(screen.getByRole('link', { name: "Today's plan" })).toHaveAttribute('href', '/today');
  });

  it('says so when nobody has replied yet', async () => {
    const db = buildFixture();
    db.linkedin.groups.forEach((g) => g.people.forEach((p) => { if (p.tab === 'replied') p.tab = 'waiting'; }));
    renderWithApp(<LinkedInPage />, { route: '/linkedin', client: new FixtureClient(db) });
    expect(await screen.findByText('No replies yet.')).toBeInTheDocument();
    expect(screen.getByText(/mark them as replied above\. Everyone you message stays recorded\./)).toBeInTheDocument();
  });
});

describe('Mark someone as replied', () => {
  it('offers only people with a request logged and no reply, by name or company', async () => {
    await setup();
    fireEvent.change(markBox(), { target: { value: 'mintlify' } });
    expect(picks().map((b) => b.textContent?.split(' at ')[0])).toEqual(['Luis Romero', 'Grace Kim', 'Ibrahim Sall']);
    fireEvent.change(markBox(), { target: { value: 'hannah' } }); // never messaged
    expect(picks()).toHaveLength(0);
    expect(screen.getByText('Nobody you messaged matches "hannah".')).toBeInTheDocument();
    fireEvent.change(markBox(), { target: { value: 'mira' } }); // already replied
    expect(picks()).toHaveLength(0);
    fireEvent.change(markBox(), { target: { value: 'l' } }); // one letter shows nothing yet
    expect(picks()).toHaveLength(0);
  });

  it('picking someone asks the reply kind, then logs the reply and lists them', async () => {
    const { spy, idOf } = await setup();
    fireEvent.change(markBox(), { target: { value: 'luis' } });
    const [luis] = picks();
    expect(luis).toHaveTextContent('Luis Romero at Mintlify, requested');
    fireEvent.click(luis as HTMLElement);
    const dialog = await screen.findByRole('dialog', { name: 'What kind of reply?' });
    expect(within(dialog).getByText(/From Luis Romero on LinkedIn/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Interested' }));
    expect(await screen.findByText('Logged: reply from Luis Romero (Interested).')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();
    // He joins the list at once, with the accept the reply implies, and the counts line moves.
    await waitFor(() => expect(queryRow('Luis Romero')).not.toBeNull());
    expect(row('Luis Romero')).toHaveTextContent('Replied today');
    expect(screen.getByText(/^Last 7 days:/)).toHaveTextContent('8 people messaged, 3 replied');
    expect(markBox()).toHaveValue('');
    expect(spy).not.toHaveBeenCalled();
    pagehide();
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy).toHaveBeenCalledWith(idOf('Luis Romero'), 'replied', expect.objectContaining({ requestId: expect.any(String) }), 'interested');
  });

  it('Cancel logs nothing', async () => {
    const { spy } = await setup();
    fireEvent.change(markBox(), { target: { value: 'rune' } });
    fireEvent.click(picks()[0] as HTMLElement);
    const dialog = await screen.findByRole('dialog', { name: 'What kind of reply?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    pagehide();
    await new Promise((r) => setTimeout(r, 20));
    expect(spy).not.toHaveBeenCalled();
    expect(queryRow('Rune Sorensen')).toBeNull();
  });
});

describe('LinkedIn list, All recorded', () => {
  it('r on Hannah Cole logs request_sent with Undo, and she stays where she is, selected', async () => {
    const { spy, idOf } = await setupAll();
    fireEvent.click(row('Hannah Cole'));
    press('r');
    expect(await screen.findByText('Logged: request sent to Hannah Cole.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();
    expect(row('Hannah Cole')).toHaveAttribute('aria-selected', 'true');
    expect(row('Hannah Cole')).toHaveTextContent('Requested today');
    expect(spy).not.toHaveBeenCalled();
    pagehide();
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy).toHaveBeenCalledWith(idOf('Hannah Cole'), 'request_sent', expect.objectContaining({ requestId: expect.any(String) }));
  });

  it('Undo puts Hannah back at Not started and logs nothing', async () => {
    const { spy } = await setupAll();
    fireEvent.click(row('Hannah Cole'));
    press('r');
    await waitFor(() => expect(row('Hannah Cole')).toHaveTextContent('Requested today'));
    fireEvent.click(await screen.findByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(row('Hannah Cole')).toHaveTextContent('Not started'));
    pagehide();
    await new Promise((r) => setTimeout(r, 20));
    expect(spy).not.toHaveBeenCalled();
  });

  it('a held r logs once', async () => {
    const { spy } = await setupAll();
    fireEvent.click(row('Hannah Cole'));
    press('r');
    press('r', { repeat: true });
    await screen.findByText('Logged: request sent to Hannah Cole.');
    pagehide();
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 20));
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('r, a, m and y log only the next step, or a reply once the request is logged', async () => {
    const { spy } = await setupAll();
    // Toasts from earlier tests can still be on screen, so count against what is there now.
    const logged = () => screen.queryAllByText(/^Logged:/).length;
    const undos = () => screen.queryAllByRole('button', { name: 'Undo' }).length;
    const [loggedBefore, undosBefore] = [logged(), undos()];
    // Hannah Cole has nothing logged: only the request is next, and no reply without one.
    fireEvent.click(row('Hannah Cole'));
    for (const key of ['a', 'm']) press(key);
    expect(await screen.findByText('Next for Hannah Cole: log request (R).')).toBeInTheDocument();
    press('y');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect([logged(), undos()]).toEqual([loggedBefore, undosBefore]);
    expect(row('Hannah Cole')).toHaveTextContent('Not started');

    // Grace Kim asked and was accepted: r and a are behind her.
    fireEvent.click(screen.getByText('Grace Kim'));
    press('r');
    expect(await screen.findByText('Already logged: request sent')).toBeInTheDocument();
    press('a');
    expect(await screen.findByText('Already logged: accepted')).toBeInTheDocument();
    expect([logged(), undos()]).toEqual([loggedBefore, undosBefore]);
    pagehide();
    await new Promise((r) => setTimeout(r, 20));
    expect(spy).not.toHaveBeenCalled();

    press('m');
    expect(await screen.findByText('Logged: message sent to Grace Kim.')).toBeInTheDocument();
    pagehide();
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy.mock.calls[0]?.[1]).toBe('message_sent');
  });

  it('y on a person who already replied says so and opens no dialog', async () => {
    const { spy } = await setup();
    fireEvent.click(screen.getByText('Anneke Visser'));
    press('y');
    expect(await screen.findByText('Already logged: reply')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(spy).not.toHaveBeenCalled();
  });

  it('y asks for the reply kind before logging, and the reply joins the replies list', async () => {
    const { spy, idOf } = await setupAll();
    fireEvent.click(screen.getByText('Ibrahim Sall'));
    press('y');
    const dialog = await screen.findByRole('dialog', { name: 'What kind of reply?' });
    for (const name of ['Interested', 'Not now', 'Said no', 'Asked to stop']) {
      expect(within(dialog).getByRole('button', { name })).toBeInTheDocument();
    }
    expect(row('Ibrahim Sall')).toHaveTextContent('Messaged Mon Nov 9');
    expect(screen.queryByText(/^Logged: reply/)).toBeNull();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Interested' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(await screen.findByText('Logged: reply from Ibrahim Sall (Interested).')).toBeInTheDocument();
    expect(row('Ibrahim Sall')).toHaveTextContent('Replied today');
    press('v');
    await waitFor(() => expect(queryRow('Hannah Cole')).toBeNull());
    expect(row('Ibrahim Sall')).toBeInTheDocument();

    pagehide();
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy).toHaveBeenCalledWith(idOf('Ibrahim Sall'), 'replied', expect.objectContaining({ requestId: expect.any(String) }), 'interested');
  });

  it('Said no logs at once, without Undo', async () => {
    const { spy, idOf } = await setupAll();
    fireEvent.click(screen.getByText('Ibrahim Sall'));
    press('y');
    const dialog = await screen.findByRole('dialog', { name: 'What kind of reply?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Said no' }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy).toHaveBeenCalledWith(idOf('Ibrahim Sall'), 'replied', expect.objectContaining({ requestId: expect.any(String) }), 'said_no');
    expect(await screen.findByText('Logged: reply from Ibrahim Sall (Said no).')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
  });

  it('Browserbase is held by Priya and its rows ask "Wait for Priya?"', async () => {
    await setupAll();
    const header = screen.getByRole('link', { name: 'Browserbase' }).closest('tr');
    if (!header) throw new Error('no Browserbase header');
    expect(header).toHaveTextContent('Held: Priya replied by email');
    expect(row('Owen Clarke')).toHaveTextContent('Wait for Priya?');
    expect(row('Maya Ellison')).toHaveTextContent('Wait for Priya?');
    expect(row('Hannah Cole')).not.toHaveTextContent('Wait for');
  });

  it('builds the hold words from heldBy.personName', async () => {
    const db = buildFixture();
    const group = db.linkedin.groups.find((g) => g.company.name === 'Browserbase');
    if (!group?.heldBy) throw new Error('fixture lost the Browserbase hold');
    group.heldBy = { ...group.heldBy, personName: 'Asha Varma' };
    await setupAll(new FixtureClient(db));
    expect(row('Owen Clarke')).toHaveTextContent('Wait for Asha?');
    expect(screen.getByText('Held: Asha replied by email')).toBeInTheDocument();
  });

  it('c copies the role title for the selected person and l opens their profile', async () => {
    const user = userEvent.setup();
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    await setupAll();
    await user.click(screen.getByText('Ethan Wu'));
    press('c');
    expect(await screen.findByText('Copied: Software Engineer, New Grad')).toBeInTheDocument();
    await expect(navigator.clipboard.readText()).resolves.toBe('Software Engineer, New Grad');

    press('l');
    expect(open).toHaveBeenCalledWith('https://linkedin.example/in/ethan-wu', '_blank', 'noopener,noreferrer');
    open.mockRestore();
  });

  it('l opens nothing when the saved profile address is not a web address', async () => {
    class Unsafe extends FixtureClient {
      override async linkedin() {
        const v = await super.linkedin();
        return {
          ...v,
          groups: v.groups.map((g) => ({
            ...g,
            people: g.people.map((p) => (p.person.name === 'Ethan Wu' ? { ...p, person: { ...p.person, linkedinUrl: 'javascript:alert(1)' } } : p)),
          })),
        };
      }
    }
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    await setupAll(new Unsafe());
    fireEvent.click(screen.getByText('Ethan Wu'));
    press('l');
    expect(await screen.findByText('No LinkedIn profile saved for Ethan Wu.')).toBeInTheDocument();
    expect(open).not.toHaveBeenCalled();
    expect(document.querySelector('a[href^="javascript:"]')).toBeNull();
    open.mockRestore();
  });

  it('filters people or companies', async () => {
    await setupAll();
    fireEvent.change(screen.getByRole('textbox', { name: 'Filter' }), { target: { value: 'graphite' } });
    await waitFor(() => expect(queryRow('Hannah Cole')).toBeNull());
    expect(screen.getByText('Percival Moon')).toBeInTheDocument();
  });
});

describe('LinkedIn list pages', () => {
  // many_linkedin: 52 replied across 26 companies. Page 1 is the six mockup companies plus Pylon, Cerebrium,
  // Trigger.dev and Baseten (20 people); page 2 runs Inngest to Tailscale (20); page 3 Sourcegraph to Neon (12).
  const where = () => screen.getByTestId('where').textContent ?? '';
  const pageParam = () => new URLSearchParams(where().split('?')[1] ?? '').get('page');
  const pageLine = () => screen.getByText(/^Page \d+ of \d+$/).textContent;
  const companyLink = (name: string) => within(table()).queryByRole('link', { name });

  function Where() {
    const location = useLocation();
    return <p data-testid="where">{location.pathname + location.search}</p>;
  }

  async function setupMany(route = '/linkedin', queryClient?: QueryClient) {
    const client = new FixtureClient(buildFixture('many_linkedin'));
    const data = await client.linkedin();
    renderWithApp(
      <>
        <LinkedInPage />
        <Where />
      </>,
      { route, client, queryClient },
    );
    await screen.findByText(/^Showing \d+ to \d+ of \d+ people$/);
    const peopleAt = (company: string) => {
      const g = data.groups.find((x) => x.company.name === company);
      if (!g) throw new Error(`no group ${company}`);
      return g.people.filter((p) => p.tab === 'replied').map((p) => ({ id: p.person.id, name: p.person.name }));
    };
    const first = (company: string) => {
      const p = peopleAt(company)[0];
      if (!p) throw new Error(`nobody at ${company}`);
      return p;
    };
    return { client, data, peopleAt, first };
  }

  it('pages by company, ten companies a page, and says who is showing out of 52', async () => {
    await setupMany();
    expect(pageLine()).toBe('Page 1 of 3');
    expect(screen.getByText('Showing 1 to 20 of 52 people')).toBeInTheDocument();
    const headers = within(table()).getAllByRole('link').filter((a) => a.getAttribute('href')?.startsWith('/companies/'));
    expect(headers.map((a) => a.textContent)).toEqual([
      'Mintlify', 'Browserbase', 'Resend', 'Supabase', 'Graphite', 'Cartesia', 'Pylon', 'Cerebrium', 'Trigger.dev', 'Baseten',
    ]);
    expect(screen.getByRole('button', { name: /^Previous/ })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: /^Next/ }));
    await waitFor(() => expect(pageLine()).toBe('Page 2 of 3'));
    expect(screen.getByText('Showing 21 to 40 of 52 people')).toBeInTheDocument();
    expect(companyLink('Inngest')).not.toBeNull();
    expect(companyLink('Mintlify')).toBeNull();
    // A company is never split: all three of Inngest's people are on this page.
    expect(within(table()).getAllByRole('row').filter((r) => r.closest('tbody'))).toHaveLength(10 + 20);
  });

  it('keeps the page in the URL, so a reload lands on it', async () => {
    await setupMany('/linkedin?page=3');
    expect(pageLine()).toBe('Page 3 of 3');
    expect(screen.getByText('Showing 41 to 52 of 52 people')).toBeInTheDocument();
    expect(companyLink('Neon')).not.toBeNull();
    expect(screen.getByRole('button', { name: /^Next/ })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: /^Previous/ }));
    await waitFor(() => expect(pageParam()).toBe('2'));
    expect(pageLine()).toBe('Page 2 of 3');
  });

  it('turning All recorded on or changing the filter goes back to page 1', async () => {
    await setupMany('/linkedin?page=2');
    expect(pageLine()).toBe('Page 2 of 3');
    press('v');
    await waitFor(() => expect(where()).toContain('all=1'));
    expect(pageLine()).toBe('Page 1 of 3');
    expect(pageParam()).toBeNull();
    expect(screen.getByRole('button', { name: /^All recorded/ })).toHaveTextContent('68');

    fireEvent.click(screen.getByRole('button', { name: /^Next/ }));
    await waitFor(() => expect(pageParam()).toBe('2'));
    fireEvent.change(screen.getByRole('textbox', { name: 'Filter' }), { target: { value: 'software' } });
    await waitFor(() => expect(pageLine()).toBe('Page 1 of 3'));
    expect(pageParam()).toBeNull();
    expect(where()).toContain('q=software');
  });

  it('j on the last row of a page goes to the first row of the next page, and k comes back', async () => {
    const { first } = await setupMany();
    const baseten = first('Baseten');
    const inngest = first('Inngest');
    fireEvent.click(row(baseten.name));
    await waitFor(() => expect(row(baseten.name)).toHaveAttribute('aria-selected', 'true'));

    press('j');
    await waitFor(() => expect(pageLine()).toBe('Page 2 of 3'));
    expect(row(inngest.name)).toHaveAttribute('aria-selected', 'true');
    await waitFor(() => expect(pageParam()).toBe('2'));

    press('k');
    await waitFor(() => expect(pageLine()).toBe('Page 1 of 3'));
    expect(row(baseten.name)).toHaveAttribute('aria-selected', 'true');
    await waitFor(() => expect(pageParam()).toBeNull());
  });

  it('] and [ turn the page and select its first row, so the step keys act on a row in sight', async () => {
    const { first } = await setupMany();
    press(']');
    await waitFor(() => expect(pageLine()).toBe('Page 2 of 3'));
    await waitFor(() => expect(row(first('Inngest').name)).toHaveAttribute('aria-selected', 'true'));
    press(']');
    await waitFor(() => expect(pageLine()).toBe('Page 3 of 3'));
    press(']');
    expect(pageLine()).toBe('Page 3 of 3');
    press('[');
    await waitFor(() => expect(pageLine()).toBe('Page 2 of 3'));
  });

  it('a person selected in the URL opens on the page that holds them', async () => {
    const client = new FixtureClient(buildFixture('many_linkedin'));
    const temporal = (await client.linkedin()).groups.find((g) => g.company.name === 'Temporal')?.people[1];
    if (!temporal) throw new Error('no Temporal person');
    renderWithApp(
      <>
        <LinkedInPage />
        <Where />
      </>,
      { route: `/linkedin?sel=${temporal.person.id}`, client },
    );
    await waitFor(() => expect(pageLine()).toBe('Page 3 of 3'));
    expect(row(temporal.person.name)).toHaveAttribute('aria-selected', 'true');
    await waitFor(() => expect(pageParam()).toBe('3'));
  });

  it('when the last page empties it goes to the page before', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { peopleAt } = await setupMany('/linkedin?page=3', queryClient);
    expect(pageLine()).toBe('Page 3 of 3');
    const onPage3 = new Set(['Sourcegraph', 'Temporal', 'Render', 'PlanetScale', 'Modal', 'Neon'].flatMap((c) => peopleAt(c).map((p) => p.id)));
    // A refetch where everyone on page 3 has moved on (logged from another tab).
    queryClient.setQueryData<LinkedInView>(queryKeys.linkedin, (v) =>
      v && {
        ...v,
        groups: v.groups.map((g) => ({ ...g, people: g.people.map((p) => (onPage3.has(p.person.id) ? { ...p, tab: 'waiting' as const } : p)) })),
      },
    );
    await waitFor(() => expect(pageLine()).toBe('Page 2 of 2'));
    expect(screen.getByText('Showing 21 to 40 of 40 people')).toBeInTheDocument();
    await waitFor(() => expect(pageParam()).toBe('2'));
  });
});
