/// <reference types="node" />
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toast } from 'sonner';
import { ApiError, type ReviewItem } from '@/api';
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

/** The review scenario: every kind, the Mesh Systems and Mesh merge question, and two items settled today. */
async function openReview(db = buildFixture('review')) {
  const client = new FixtureClient(db);
  const view = renderWithApp(<Page />, { route: '/needs-you', client });
  await screen.findByRole('article', { name: 'Mesh Systems and Mesh' });
  return Object.assign(view, { client });
}

const MESH = 'Mesh Systems and Mesh';
const progress = () => screen.getByTestId('ny-progress');

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
    expect(needsYouLink()).toHaveAccessibleName(/^Needs you, 4 open/);
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
    expect(needsYouLink()).toHaveAccessibleName(/^Needs you, 4 open/);

    await user.click(screen.getByRole('button', { name: 'Undo' }));
    expect(await screen.findByRole('article', { name: 'Trigger.dev' })).toBeInTheDocument();
    expect(needsYouLink()).toHaveAccessibleName(/^Needs you, 5 open/);
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

  it('a merge item shows no No-go button and n does nothing', async () => {
    const { client } = await openReview();
    const resolve = vi.spyOn(client, 'resolveReview');
    const mesh = item(MESH);
    expect(within(mesh).queryByRole('button', { name: /Mark no-go/ })).not.toBeInTheDocument();
    expect(within(mesh).queryByRole('button', { name: /Mark ok|Leave as is/ })).not.toBeInTheDocument();
    expect(within(mesh).getByRole('button', { name: 'Keep separate' })).toBeInTheDocument();
    select(MESH);
    press('n');
    press('e');
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(within(item(MESH)).queryByRole('form')).not.toBeInTheDocument();
    expect(resolve).not.toHaveBeenCalled();
  });

  it('a possible duplicate the API could not compare still offers Keep separate and no no-go', async () => {
    const db = buildFixture();
    const base = db.needsYou.open[0]!;
    db.needsYou.open.push({
      ...base, id: 9001, kind: 'merge_candidate', company: { ...base.company, id: 9002, name: 'Bolt', picked: false },
      holdsEmail: false, reason: 'Maybe one company: Bolt (#9002) and Bolt Labs (#9003).', merge: null,
    });
    await open(new FixtureClient(db));
    const bolt = item('Bolt');
    expect(within(bolt).getByRole('button', { name: 'Keep separate' })).toBeInTheDocument();
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
    expect(row).toHaveTextContent('Edited, locked');
  });

  it('shows the empty state when nothing is open', async () => {
    openScenario('empty');
    expect(await screen.findByText('Nothing needs you.')).toBeInTheDocument();
    expect(screen.getByText('New items appear after each board read and when you pick a company.')).toBeInTheDocument();
  });

  it('the first item is selected when the page opens, so one key settles it', async () => {
    await open();
    expect(item('Cerebrium')).toHaveAttribute('aria-current', 'true');
    press('o');
    expect(await screen.findByText('Marked ok. Cerebrium still has no email domain.')).toBeInTheDocument();
    expect(item('Trigger.dev')).toHaveAttribute('aria-current', 'true');
  });

  it('sections: the review scenario shows all six in order and drops empty ones', async () => {
    await openReview();
    const titles = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent);
    expect(titles).toEqual([
      'Holding up email 2', 'Sponsorship 1', 'Company details 2', 'Job boards 2', 'Possible duplicates 1', 'Other 3', 'Resolved this week 6',
    ]);
    const other = screen.getByRole('region', { name: /^Other/ });
    expect(within(other).getAllByRole('article').map((a) => a.getAttribute('aria-label'))).toEqual(['Larkspur AI', 'Quillmate', 'Brightwell']);
  });

  it('a size item that is not holding email lands under Company details', async () => {
    await openReview();
    const details = screen.getByRole('region', { name: /^Company details/ });
    expect(within(details).getAllByRole('article').map((a) => a.getAttribute('aria-label'))).toEqual(['Fernhill', 'Copperline']);
    expect(within(screen.getByRole('region', { name: /^Holding up email/ })).getByRole('article', { name: 'Trigger.dev' })).toBeInTheDocument();
  });

  it('a size or domain item for a company nobody is emailing moves from Holding up email to Company details', async () => {
    const db = buildFixture();
    for (const r of db.needsYou.open) r.holdsEmail = false;
    await open(new FixtureClient(db));
    const details = screen.getByRole('region', { name: /^Company details/ });
    expect(within(details).getAllByRole('article').map((a) => a.getAttribute('aria-label'))).toEqual(['Cerebrium', 'Trigger.dev']);
    expect(screen.queryByRole('region', { name: /^Holding up email/ })).not.toBeInTheDocument();
  });

  it('m merges into the suggested company with an Undo toast and o keeps them separate', async () => {
    const user = userEvent.setup();
    const { client } = await openReview();
    const mesh = await reviewOf(client, 'Mesh Systems');
    const resolve = vi.spyOn(client, 'resolveReview');
    expect(needsYouLink()).toHaveAccessibleName(/^Needs you, 11 open/);

    select(MESH);
    press('m');
    expect(await screen.findByText('Merged Mesh into Mesh Systems.')).toBeInTheDocument();
    expect(screen.queryByRole('article', { name: MESH })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: /^Possible duplicates/ })).not.toBeInTheDocument();
    expect(needsYouLink()).toHaveAccessibleName(/^Needs you, 10 open/);
    expect(progress()).toHaveTextContent('3 settled today, 10 open');
    expect(resolve).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Undo' }));
    expect(await screen.findByRole('article', { name: MESH })).toBeInTheDocument();
    expect(needsYouLink()).toHaveAccessibleName(/^Needs you, 11 open/);

    select(MESH);
    press('o');
    expect(await screen.findByText('Kept Mesh Systems and Mesh separate.')).toBeInTheDocument();
    expect(screen.queryByRole('article', { name: MESH })).not.toBeInTheDocument();
    window.dispatchEvent(new Event('pagehide'));
    await waitFor(() => expect(resolve).toHaveBeenCalledTimes(1));
    expect(resolve).toHaveBeenCalledWith(mesh.id, { kind: 'ok' }, mesh.revision, expect.objectContaining({ requestId: expect.any(String) }));
  });

  it('the Merge into the other button works with the mouse and has no key', async () => {
    const user = userEvent.setup();
    const { client } = await openReview();
    const mesh = await reviewOf(client, 'Mesh Systems');
    const resolve = vi.spyOn(client, 'resolveReview');
    const other = within(item(MESH)).getByRole('button', { name: 'Merge into Mesh' });
    expect(other).not.toHaveAttribute('aria-keyshortcuts');
    expect(other.querySelector('kbd')).toBeNull();

    await user.click(other);
    expect(await screen.findByText('Merged Mesh Systems into Mesh.')).toBeInTheDocument();
    window.dispatchEvent(new Event('pagehide'));
    await waitFor(() => expect(resolve).toHaveBeenCalledTimes(1));
    expect(resolve).toHaveBeenCalledWith(mesh.id, { kind: 'merge', keep: 'b' }, mesh.revision, expect.objectContaining({ requestId: expect.any(String) }));
  });

  it('undo inside the window sends nothing', async () => {
    const user = userEvent.setup();
    const { client } = await openReview();
    const resolve = vi.spyOn(client, 'resolveReview');
    select(MESH);
    press('m');
    await user.click(await screen.findByRole('button', { name: 'Undo' }));
    expect(await screen.findByRole('article', { name: MESH })).toBeInTheDocument();
    window.dispatchEvent(new Event('pagehide'));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(resolve).not.toHaveBeenCalled();
    expect((await client.needsYou()).open.some((r) => r.kind === 'merge_candidate')).toBe(true);
  });

  it('Resolved shows Undo for an ok row and a lock with no button for an edit', async () => {
    const user = userEvent.setup();
    await open();
    await user.click(screen.getByRole('button', { name: 'Show' }));
    const railway = screen.getByRole('row', { name: /^Railway/ });
    expect(within(railway).getByRole('button', { name: 'Undo' })).toBeInTheDocument();
    expect(railway).not.toHaveTextContent('locked');
    const turso = screen.getByRole('row', { name: /^Turso/ });
    expect(within(turso).queryByRole('button')).not.toBeInTheDocument();
    expect(turso).toHaveTextContent('Edited, locked');
    expect(turso).toHaveTextContent('Set to about 45 by you, from LinkedIn 11 to 50. To change it, set it again on the company page.');
  });

  it('Resolved groups its rows by the real day', async () => {
    const user = userEvent.setup();
    await openReview();
    await user.click(screen.getByRole('button', { name: 'Show' }));
    const groupRows = screen.getAllByRole('row').filter((r) => r.querySelectorAll('td').length === 1).map((r) => r.textContent);
    expect(groupRows).toEqual(['Today', 'Earlier this week', 'Fri Nov 6', 'Thu Nov 5']);
  });

  it("Undo calls reopenReview with the row's revision and the item returns", async () => {
    const user = userEvent.setup();
    const { client } = await open();
    const railway = (await client.needsYou()).resolvedThisWeek.find((r) => r.companyName === 'Railway');
    if (!railway) throw new Error('no Railway row');
    const reopen = vi.spyOn(client, 'reopenReview');
    await user.click(screen.getByRole('button', { name: 'Show' }));
    await user.click(within(screen.getByRole('row', { name: /^Railway/ })).getByRole('button', { name: 'Undo' }));

    expect(await screen.findByText("Undone. Railway's sponsorship question is open again.")).toBeInTheDocument();
    expect(reopen).toHaveBeenCalledWith(railway.id, railway.revision, expect.objectContaining({ requestId: expect.any(String) }));
    expect(await screen.findByRole('article', { name: 'Railway' })).toBeInTheDocument();
    expect(screen.queryByRole('row', { name: /^Railway/ })).not.toBeInTheDocument();
    await waitFor(() => expect(needsYouLink()).toHaveAccessibleName(/^Needs you, 6 open/));
  });

  it('after a no-go commits and the list refetches, its Resolved row has Undo and the day counts it once', async () => {
    const user = userEvent.setup();
    const { client } = await open();
    const hex = await reviewOf(client, 'Hex');
    select('Hex');
    press('n');
    const dialog = await screen.findByRole('alertdialog', { name: 'Add Hex to the no-go list?' });
    await user.type(within(dialog).getByRole('textbox', { name: 'Source' }), 'Hex careers FAQ');
    await user.click(within(dialog).getByRole('button', { name: 'Mark no-go' }));
    await waitFor(async () => expect((await client.needsYou()).open.some((r) => r.id === hex.id)).toBe(false));
    await waitFor(() => expect(progress()).toHaveTextContent(/^1 settled today, 4 open$/));
    await user.click(screen.getByRole('button', { name: 'Show' }));
    const row = await screen.findByRole('row', { name: /^Hex/ });
    expect(await within(row).findByRole('button', { name: 'Undo' })).toBeInTheDocument();
    expect(progress()).toHaveTextContent(/^1 settled today, 4 open$/);
  });

  it('a double click on Undo in Resolved sends one reopen', async () => {
    const { client } = await open();
    const reopen = vi.spyOn(client, 'reopenReview');
    fireEvent.click(screen.getByRole('button', { name: 'Show' }));
    const undo = within(await screen.findByRole('row', { name: /^Railway/ })).getByRole('button', { name: 'Undo' });
    fireEvent.click(undo);
    fireEvent.click(undo);
    await screen.findByRole('article', { name: 'Railway' });
    expect(reopen).toHaveBeenCalledTimes(1);
  });

  it('a slow reopen changes nothing on screen until it answers, and its Undo is off meanwhile', async () => {
    const { client } = await open();
    let answer: () => void = () => undefined;
    vi.spyOn(client, 'reopenReview').mockImplementation(() => new Promise<void>((resolve) => { answer = resolve; }));
    fireEvent.click(screen.getByRole('button', { name: 'Show' }));
    const row = await screen.findByRole('row', { name: /^Railway/ });
    fireEvent.click(within(row).getByRole('button', { name: 'Undo' }));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(screen.getByRole('row', { name: /^Railway/ })).toBeInTheDocument();
    expect(within(screen.getByRole('row', { name: /^Railway/ })).getByRole('button', { name: 'Undo' })).toBeDisabled();
    expect(needsYouLink()).toHaveAccessibleName(/^Needs you, 5 open/);
    expect(screen.queryByRole('article', { name: 'Railway' })).not.toBeInTheDocument();
    await act(async () => answer());
  });

  it('a failed reopen leaves the Resolved row and the counts as they were, and says so', async () => {
    const { client } = await open();
    vi.spyOn(client, 'reopenReview').mockRejectedValue(new ApiError('conflict', 'This item changed since you loaded it. Nothing was saved.'));
    fireEvent.click(screen.getByRole('button', { name: 'Show' }));
    fireEvent.click(within(await screen.findByRole('row', { name: /^Railway/ })).getByRole('button', { name: 'Undo' }));
    expect(await screen.findByText('This item changed since you loaded it. Nothing was saved.')).toBeInTheDocument();
    const row = screen.getByRole('row', { name: /^Railway/ });
    await waitFor(() => expect(within(row).getByRole('button', { name: 'Undo' })).toBeEnabled());
    expect(needsYouLink()).toHaveAccessibleName(/^Needs you, 5 open/);
    expect(progress()).toHaveTextContent(/^5 open$/);
  });

  it('Undo on a merge from Resolved splits the two companies again', async () => {
    const user = userEvent.setup();
    await openReview();
    await user.click(screen.getByRole('button', { name: 'Show' }));
    const sorrel = screen.getByRole('row', { name: /^Sorrel/ });
    expect(sorrel).toHaveTextContent('Merged Sorrel AI into Sorrel by you');
    await user.click(within(sorrel).getByRole('button', { name: 'Undo' }));
    expect(await screen.findByText("Undone. The merge is taken back, and Sorrel's duplicate question is open again.")).toBeInTheDocument();
    expect(await screen.findByRole('article', { name: 'Sorrel and Sorrel AI' })).toBeInTheDocument();
  });

  it('settling the last item moves focus to the All settled heading and the live region says "All settled."', async () => {
    const db = buildFixture();
    db.needsYou.open = db.needsYou.open.filter((r) => r.company.name === 'Hex');
    await renderWithApp(<Page />, { route: '/needs-you', client: new FixtureClient(db) });
    await screen.findByRole('article', { name: 'Hex' });
    expect(progress()).toHaveTextContent(/^1 open$/);
    select('Hex');
    press('o');
    const heading = await screen.findByRole('heading', { level: 2, name: 'All settled.' });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(screen.getByRole('status')).toHaveTextContent('All settled.');
    expect(screen.getByText('You settled 1 today.')).toBeInTheDocument();
    expect(screen.queryByTestId('ny-progress')).not.toBeInTheDocument();
  });

  it('the settled panel lists what was settled today and the next board read', async () => {
    const db = buildFixture('review');
    db.needsYou.open = [];
    renderWithApp(<Page />, { route: '/needs-you', client: new FixtureClient(db) });
    const panel = await screen.findByRole('region', { name: 'All settled.' });
    expect(within(panel).getByText('You settled 2 today.')).toBeInTheDocument();
    const lines = within(panel).getAllByRole('listitem');
    expect(lines.map((l) => l.textContent)).toEqual([
      'WrenfieldEmail domain set to wrenfield.example by you, from the company website footerLocked09:12',
      'AmbergateMarked ok by you09:20',
    ]);
    expect(
      within(panel).getByText('Boards are read again Thu Nov 12 at 08:00. New items appear after each board read and when you pick a company.'),
    ).toBeInTheDocument();
    expect(within(panel).getByRole('link', { name: /^Open the Outbox/ })).toHaveAttribute('href', '/outbox');
    expect(within(panel).getByText('7 drafts wait for review there.')).toBeInTheDocument();
    expect(screen.queryByText('Nothing needs you.')).not.toBeInTheDocument();
    expect(screen.queryByTestId('ny-progress')).not.toBeInTheDocument();
  });

  it('the progress line reads plain counts and has no streak copy', async () => {
    const user = userEvent.setup();
    await open();
    expect(progress()).toHaveTextContent(/^5 open$/);
    select('Hex');
    press('o');
    await waitFor(() => expect(progress()).toHaveTextContent(/^1 settled today, 4 open$/));
    expect(progress().textContent).not.toMatch(/streak|missed|overdue|behind|keep it up/i);
    await user.click(screen.getByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(progress()).toHaveTextContent(/^5 open$/));
  });

  it('the review scenario progress line counts what was settled today', async () => {
    await openReview();
    expect(progress()).toHaveTextContent(/^2 settled today, 11 open$/);
  });

  it('Save and lock stays disabled for a size with no digit and a domain with no dot', async () => {
    const user = userEvent.setup();
    await open();
    select('Trigger.dev');
    press('e');
    const size = await within(item('Trigger.dev')).findByRole('textbox', { name: 'Size' });
    await user.type(within(item('Trigger.dev')).getByRole('textbox', { name: 'Source' }), 'LinkedIn');
    await user.type(size, 'lots');
    const save = within(item('Trigger.dev')).getByRole('button', { name: 'Save and lock' });
    expect(save).toHaveAttribute('aria-disabled', 'true');
    await user.clear(size);
    await user.type(size, 'about 45');
    expect(save).toHaveAttribute('aria-disabled', 'false');
    expect(within(item('Trigger.dev')).getByText('A number or a band, like 34, about 45, or 11 to 50.')).toBeInTheDocument();
    await user.click(within(item('Trigger.dev')).getByRole('button', { name: 'Cancel' }));

    select('Cerebrium');
    press('e');
    const domain = await within(item('Cerebrium')).findByRole('textbox', { name: 'Email domain' });
    await user.type(within(item('Cerebrium')).getByRole('textbox', { name: 'Source' }), 'Website');
    await user.type(domain, 'cerebrium');
    const saveDomain = within(item('Cerebrium')).getByRole('button', { name: 'Save and lock' });
    expect(saveDomain).toHaveAttribute('aria-disabled', 'true');
    await user.type(domain, '.example');
    expect(saveDomain).toHaveAttribute('aria-disabled', 'false');
  });

  it('the sponsorship form offers Yes, OPT only, Unclear and No, and OPT only saves as written', async () => {
    const user = userEvent.setup();
    const { client } = await open();
    const hex = await reviewOf(client, 'Hex');
    const resolve = vi.spyOn(client, 'resolveReview');
    select('Hex');
    press('e');
    const group = await within(item('Hex')).findByRole('radiogroup', { name: 'Sponsorship' });
    const radios = within(group).getAllByRole('radio');
    expect(radios.map((r) => document.querySelector(`label[for="${r.id}"]`)?.textContent)).toEqual(['Yes', 'OPT only', 'Unclear', 'No']);
    await user.click(within(group).getByRole('radio', { name: 'OPT only' }));
    await user.type(within(item('Hex')).getByRole('textbox', { name: 'Source' }), 'Recruiter on LinkedIn');
    await user.click(within(item('Hex')).getByRole('button', { name: 'Save and lock' }));
    expect(await screen.findByText("Saved and locked. Hex's sponsorship is OPT only.")).toBeInTheDocument();
    window.dispatchEvent(new Event('pagehide'));
    await waitFor(() => expect(resolve).toHaveBeenCalledTimes(1));
    expect(resolve).toHaveBeenCalledWith(hex.id, { kind: 'edit', field: 'sponsorship', value: 'OPT only', source: 'Recruiter on LinkedIn' }, hex.revision, expect.anything());
  });

  it('says what this page is for in one line', async () => {
    await openReview();
    expect(screen.getByText('Things only you can settle. Your answer sticks.')).toBeInTheDocument();
  });

  it('the fix is the one filled button, "ok" is outlined with a word for its kind, and Mark no-go is quiet', async () => {
    await openReview();
    const variant = (el: HTMLElement) => el.getAttribute('data-variant');
    const hex = within(item('Hex'));
    expect(variant(hex.getByRole('button', { name: 'Set sponsorship' }))).toBe('default');
    expect(variant(hex.getByRole('button', { name: 'Leave as is' }))).toBe('outline');
    expect(variant(hex.getByRole('button', { name: 'Mark no-go' }))).toBe('ghost');
    expect(hex.getByRole('button', { name: 'Mark no-go' }).className).not.toMatch(/\bborder-border\b/);
    // Per kind, so the button says what it leaves.
    expect(within(item('Fernhill')).getByRole('button', { name: 'Size is fine' })).toBeInTheDocument();
    expect(within(item('Copperline')).getByRole('button', { name: 'Size is fine' })).toBeInTheDocument();
    // An item with no field to fix has nothing filled: settling it is the outlined button.
    const quill = within(item('Quillmate'));
    expect(quill.queryAllByRole('button').filter((b) => variant(b) === 'default')).toHaveLength(0);
    expect(variant(quill.getByRole('button', { name: 'Leave as is' }))).toBe('outline');
    // Every "ok" keeps the O key and says "Mark ok" on hover.
    expect(quill.getByRole('button', { name: 'Leave as is' })).toHaveAttribute('aria-keyshortcuts', 'o');
    expect(quill.getByRole('button', { name: 'Leave as is' })).toHaveAttribute('title', 'Mark ok');
  });

  it('the new kinds show their words and icon and cannot crash the view', async () => {
    await openReview();
    const words: [string, string][] = [
      ['Fernhill', 'Size is a guess'], ['Copperline', 'May be a subsidiary'], [MESH, 'Maybe one company'],
      ['Larkspur AI', 'Reader disagrees'], ['Quillmate', 'Flagged'], ['Brightwell', 'Careers page changed'],
    ];
    for (const [name, word] of words) {
      const a = item(name);
      expect(within(a).getByText(word)).toBeInTheDocument();
      expect(a.querySelector('svg[data-icon]')).not.toBeNull();
    }
    expect(within(item('Fernhill')).getByRole('button', { name: 'Set size' })).toBeInTheDocument();
    expect(within(item('Copperline')).getByRole('button', { name: 'Set size' })).toBeInTheDocument();
    for (const name of ['Larkspur AI', 'Quillmate', 'Brightwell']) {
      expect(within(item(name)).queryByRole('button', { name: /^Set / })).not.toBeInTheDocument();
      expect(within(item(name)).getByRole('button', { name: 'Leave as is' })).toBeInTheDocument();
    }
    expect(item('Larkspur AI')).toHaveTextContent('"We are unable to sponsor work visas for this position."');
  });

  it('nothing on the page is red, the domain badge and the board reads included', async () => {
    const user = userEvent.setup();
    const { container } = await openReview();
    await user.click(screen.getByRole('button', { name: 'Show' }));
    const red = container.querySelectorAll('[class*="-bad"], [data-variant="bad"], [data-variant="destructive"]');
    expect([...red].map((el) => el.outerHTML.slice(0, 80))).toEqual([]);
    expect(within(item('Cerebrium')).getByText('Missing email domain').closest('[data-slot="badge"]')).toHaveAttribute('data-variant', 'warn');
  });

  it('no new string holds a dash or a guilt word', () => {
    const dir = path.resolve(import.meta.dirname);
    const files = readdirSync(dir).filter((f) => /\.tsx?$/.test(f) && !/\.test\./.test(f));
    expect(files).toEqual(expect.arrayContaining(['index.tsx', 'review-item.tsx', 'merge-compare.tsx', 'settled-panel.tsx']));
    for (const f of files) {
      const text = readFileSync(path.join(dir, f), 'utf8');
      expect(text).not.toMatch(/[\u2013\u2014]/u);
      const strings = text.match(/(['"`])(?:(?!\1).)*\1|>[^<>{}]+</g) ?? [];
      expect(strings.filter((x) => /\b(?:streaks?|missed|overdue|behind|keep it up)\b/i.test(x))).toEqual([]);
    }
  });
});
