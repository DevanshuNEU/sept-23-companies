import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toast } from 'sonner';
import type { ReviewItem } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture, type Scenario } from '@/api/fixture/data';
import { SidebarProvider } from '@/components/ui/sidebar';
import { AppSidebar } from '@/shell/app-sidebar';
import { renderWithApp } from '@/test/render';
import NeedsYouPage from './index';

/** Needs you next to the real sidebar, so the amber count by Needs you can be watched. */
function Page() {
  return (
    <SidebarProvider>
      <AppSidebar />
      <NeedsYouPage />
    </SidebarProvider>
  );
}

async function open(client = new FixtureClient()) {
  const view = renderWithApp(<Page />, { route: '/needs-you', client });
  await screen.findByRole('article', { name: 'Cerebrium' });
  return Object.assign(view, { client: client as FixtureClient });
}

function openScenario(scenario: Scenario) {
  return renderWithApp(<Page />, { route: '/needs-you', client: new FixtureClient(buildFixture(scenario)) });
}

const press = (key: string, init: KeyboardEventInit = {}) => fireEvent.keyDown(document.body, { key, ...init });
const item = (name: string) => screen.getByRole('article', { name });
const select = (name: string) => fireEvent.click(item(name));
const sidebar = () => document.querySelector<HTMLElement>('[data-slot="sidebar"]') ?? document.body;
const needsYouLink = () => within(sidebar()).getByRole('link', { name: /^Needs you/ });

async function reviewOf(client: FixtureClient, name: string): Promise<ReviewItem> {
  const found = (await client.needsYou()).open.find((r) => r.company.name === name);
  if (!found) throw new Error(`No review item for ${name}`);
  return found;
}

// Sonner keeps its toasts in a module, so one test's undo toast would show in the next.
afterEach(() => {
  toast.dismiss();
});

describe('Needs you', () => {
  it('groups the items by what they hold up, in order', async () => {
    await open();
    const titles = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent);
    expect(titles).toEqual(['Holding up email 2', 'Sponsorship 1', 'Job boards 2', 'Resolved this week 6']);

    const email = screen.getByRole('region', { name: /^Holding up email/ });
    expect(within(email).getAllByRole('article').map((a) => a.getAttribute('aria-label'))).toEqual(['Cerebrium', 'Trigger.dev']);
    const boards = screen.getByRole('region', { name: /^Job boards/ });
    expect(within(boards).getAllByRole('article').map((a) => a.getAttribute('aria-label'))).toEqual(['Zed Industries', 'PlanetScale']);
  });

  it('shows each item with its trigger, context, age and evidence', async () => {
    await open();
    const cerebrium = item('Cerebrium');
    expect(within(cerebrium).getByText('Missing email domain')).toBeInTheDocument();
    expect(within(cerebrium).getByText('Picked today')).toBeInTheDocument();

    const zed = item('Zed Industries');
    expect(within(zed).getByText('Waiting 11 days')).toBeInTheDocument();
    expect(within(zed).getByRole('img', { name: 'ok, error, error, error, error, error' })).toBeInTheDocument();
    expect(zed).toHaveTextContent('Last 6 reads');

    const hex = item('Hex');
    expect(within(hex).getByText('Opened Mon Nov 9')).toBeInTheDocument();
    expect(hex).toHaveTextContent('"We may be able to support visa sponsorship for exceptional candidates."');
    expect(within(hex).getByRole('link', { name: 'Open the posting' })).toHaveAttribute('href', expect.stringMatching(/^https:/));
  });

  it('e opens the inline form; Save and lock waits for a source, then resolves with the edit', async () => {
    const user = userEvent.setup();
    const { client } = await open();
    const cerebrium = await reviewOf(client, 'Cerebrium');
    const resolve = vi.spyOn(client, 'resolveReview');

    select('Cerebrium');
    press('e');
    const domain = await within(item('Cerebrium')).findByRole('textbox', { name: 'Email domain' });
    expect(domain).toHaveFocus();
    const save = within(item('Cerebrium')).getByRole('button', { name: 'Save and lock' });
    expect(save).toHaveAttribute('aria-disabled', 'true');

    await user.type(domain, 'cerebrium.example');
    expect(save).toHaveAttribute('aria-disabled', 'true');
    await user.click(save);
    expect(item('Cerebrium')).toBeInTheDocument();

    await user.type(within(item('Cerebrium')).getByRole('textbox', { name: 'Source' }), 'Company website footer');
    expect(save).toHaveAttribute('aria-disabled', 'false');
    await user.click(save);

    expect(await screen.findByText("Saved and locked. Cerebrium's drafts will be checked now.")).toBeInTheDocument();
    expect(screen.queryByRole('article', { name: 'Cerebrium' })).not.toBeInTheDocument();
    expect(needsYouLink()).toHaveAccessibleName('Needs you, 4 open');
    expect(resolve).not.toHaveBeenCalled();

    // Leaving the page commits what waits in the undo window.
    window.dispatchEvent(new Event('pagehide'));
    await waitFor(() => expect(resolve).toHaveBeenCalledTimes(1));
    expect(resolve).toHaveBeenCalledWith(
      cerebrium.id,
      { kind: 'edit', field: 'domain', value: 'cerebrium.example', source: 'Company website footer' },
      cerebrium.revision,
      expect.objectContaining({ requestId: expect.any(String) }),
    );
  });

  it('mod+enter saves and locks from inside the form', async () => {
    const user = userEvent.setup();
    await open();
    select('Trigger.dev');
    press('e');
    const size = await within(item('Trigger.dev')).findByRole('textbox', { name: 'Size' });
    await user.type(size, 'about 30');
    const source = within(item('Trigger.dev')).getByRole('textbox', { name: 'Source' });
    await user.type(source, 'Team page, counted by hand');
    fireEvent.keyDown(source, { key: 'Enter', ctrlKey: true });
    await waitFor(() => expect(screen.queryByRole('article', { name: 'Trigger.dev' })).not.toBeInTheDocument());
  });

  it('single keys typed in the form do nothing', async () => {
    const user = userEvent.setup();
    const { client } = await open();
    const resolve = vi.spyOn(client, 'resolveReview');
    select('Cerebrium');
    press('e');
    const domain = await within(item('Cerebrium')).findByRole('textbox', { name: 'Email domain' });
    await user.type(domain, 'one');
    expect(domain).toHaveValue('one');
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(screen.queryByText(/^Marked ok/)).not.toBeInTheDocument();
    expect(resolve).not.toHaveBeenCalled();
  });

  it('o marks the selected item ok with Undo', async () => {
    const user = userEvent.setup();
    const { client } = await open();
    const resolve = vi.spyOn(client, 'resolveReview');

    select('Trigger.dev');
    press('o');
    expect(await screen.findByText('Marked ok. Trigger.dev stays at unknown size.')).toBeInTheDocument();
    expect(screen.queryByRole('article', { name: 'Trigger.dev' })).not.toBeInTheDocument();
    expect(needsYouLink()).toHaveAccessibleName('Needs you, 4 open');

    await user.click(screen.getByRole('button', { name: 'Undo' }));
    expect(await screen.findByRole('article', { name: 'Trigger.dev' })).toBeInTheDocument();
    expect(needsYouLink()).toHaveAccessibleName('Needs you, 5 open');
    expect(resolve).not.toHaveBeenCalled();
  });

  it('n opens the no-go dialog, which needs a source', async () => {
    const user = userEvent.setup();
    const { client } = await open();
    const hex = await reviewOf(client, 'Hex');
    const resolve = vi.spyOn(client, 'resolveReview');

    select('Hex');
    press('n');
    const dialog = await screen.findByRole('alertdialog', { name: 'Add Hex to the no-go list?' });
    expect(within(dialog).getByRole('radio', { name: 'Does not sponsor' })).toBeChecked();
    const mark = within(dialog).getByRole('button', { name: 'Mark no-go' });
    expect(mark).toHaveAttribute('aria-disabled', 'true');
    await user.click(mark);
    expect(resolve).not.toHaveBeenCalled();

    await user.type(within(dialog).getByRole('textbox', { name: 'Source' }), 'Hex careers FAQ');
    expect(mark).toHaveAttribute('aria-disabled', 'false');
    await user.click(mark);

    await waitFor(() => expect(resolve).toHaveBeenCalledTimes(1));
    expect(resolve).toHaveBeenCalledWith(
      hex.id,
      { kind: 'no_go', reason: 'no_sponsorship', source: 'Hex careers FAQ' },
      hex.revision,
      expect.objectContaining({ requestId: expect.any(String) }),
    );
    expect(await screen.findByText('Hex is on the no-go list.')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
    expect(screen.queryByRole('article', { name: 'Hex' })).not.toBeInTheDocument();
  });

  it('a possible duplicate has no Mark no-go, by button or by n', async () => {
    const db = buildFixture();
    const base = db.needsYou.open[0]!;
    db.needsYou.open.push({
      ...base, id: 9001, kind: 'merge_candidate', company: { ...base.company, id: 9002, name: 'Bolt', picked: false },
      holdsEmail: false, reason: 'Maybe one company: Bolt (#9002) and Bolt Labs (#9003).',
    });
    await open(new FixtureClient(db));
    const bolt = item('Bolt');
    expect(within(bolt).getByRole('button', { name: /Mark ok/ })).toBeInTheDocument();
    expect(within(bolt).queryByRole('button', { name: /Mark no-go/ })).not.toBeInTheDocument();
    select('Bolt');
    press('n');
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('setting sponsorship to No opens the same no-go dialog', async () => {
    const user = userEvent.setup();
    await open();
    select('Hex');
    press('e');
    await user.click(await within(item('Hex')).findByRole('radio', { name: 'No' }));
    await user.type(within(item('Hex')).getByRole('textbox', { name: 'Source' }), 'Recruiter said so');
    await user.click(within(item('Hex')).getByRole('button', { name: 'Save and lock' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Add Hex to the no-go list?' });
    expect(within(dialog).getByRole('radio', { name: 'Does not sponsor' })).toBeChecked();
    expect(within(dialog).getByRole('textbox', { name: 'Source' })).toHaveValue('Recruiter said so');
  });

  it('lists what was resolved this week behind Show', async () => {
    const user = userEvent.setup();
    await open();
    expect(screen.queryByText('Set to about 45 by you, from LinkedIn 11 to 50')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Show' }));
    const row = screen.getByText('Set to about 45 by you, from LinkedIn 11 to 50').closest('tr');
    expect(row).toHaveTextContent('Turso');
    expect(row).toHaveTextContent('Unknown size');
    expect(row).toHaveTextContent('Fri Nov 6');
    expect(row).toHaveTextContent('Locked');
  });

  it('shows the empty state when nothing is open', async () => {
    openScenario('empty');
    expect(await screen.findByText('Nothing needs you.')).toBeInTheDocument();
    expect(screen.getByText('New items appear after each board read and when you pick a company.')).toBeInTheDocument();
  });
});
