import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toast } from 'sonner';
import type { ReviewItem } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture } from '@/api/fixture/data';
import { SidebarProvider } from '@/components/ui/sidebar';
import { AppSidebar } from '@/shell/app-sidebar';
import { renderWithApp } from '@/test/render';
import NeedsYouPage from './index';

function Page() {
  return (
    <SidebarProvider>
      <AppSidebar />
      <NeedsYouPage />
    </SidebarProvider>
  );
}

/**
 * Cerebrium already has a missing-domain item. This adds a size item for the same company, the way the store does
 * when two producers each raise their own question about it (#288).
 */
async function openTwoQuestions() {
  const db = buildFixture();
  const trigger = db.needsYou.open.find((r) => r.company.name === 'Trigger.dev');
  const cerebrium = db.needsYou.open.find((r) => r.company.name === 'Cerebrium');
  if (!trigger || !cerebrium) throw new Error('fixture changed');
  const size: ReviewItem = {
    ...structuredClone(trigger),
    id: 9001,
    company: cerebrium.company,
    reason: 'You picked Cerebrium, and nobody has recorded its size.',
    sizeFacts: [],
  };
  db.needsYou.open.splice(1, 0, size);
  db.needsYou.open = db.needsYou.open.filter((r) => r.id !== trigger.id);
  const client = new FixtureClient(db);
  renderWithApp(<Page />, { route: '/needs-you', client });
  await screen.findByRole('article', { name: 'Cerebrium' });
  return { client, domainItem: cerebrium, sizeItem: size };
}

const item = (name: string) => screen.getByRole('article', { name });
const press = (key: string) => fireEvent.keyDown(document.body, { key });

afterEach(() => {
  toast.dismiss();
});

describe('Needs you: one row per company for size and domain (#288)', () => {
  it('shows a company with two open questions as one row that keeps both reasons', async () => {
    await openTwoQuestions();
    expect(screen.getAllByRole('article', { name: 'Cerebrium' })).toHaveLength(1);
    const row = item('Cerebrium');
    expect(within(row).getByText('Missing email domain')).toBeInTheDocument();
    expect(within(row).getByText('Unknown size')).toBeInTheDocument();
    expect(row).toHaveTextContent('nobody has recorded its size');
    expect(row).toHaveTextContent('No email domain on file');
    expect(within(row).getAllByRole('button', { name: /Set size and domain/ })).toHaveLength(1);
  });

  it('e opens one form with Size, Email domain and one Source, and saves both in one go', async () => {
    const user = userEvent.setup();
    const { client, domainItem, sizeItem } = await openTwoQuestions();
    const resolve = vi.spyOn(client, 'resolveReview');

    fireEvent.click(item('Cerebrium'));
    press('e');
    const row = item('Cerebrium');
    const save = await within(row).findByRole('button', { name: 'Save and lock' });
    expect(within(row).getAllByRole('textbox', { name: 'Source' })).toHaveLength(1);
    expect(save).toHaveAttribute('aria-disabled', 'true');

    await user.type(within(row).getByRole('textbox', { name: 'Size' }), 'about 30');
    await user.type(within(row).getByRole('textbox', { name: 'Email domain' }), 'cerebrium.example');
    expect(save).toHaveAttribute('aria-disabled', 'true');
    await user.type(within(row).getByRole('textbox', { name: 'Source' }), 'Company website');
    expect(save).toHaveAttribute('aria-disabled', 'false');
    await user.click(save);

    expect(screen.queryByRole('article', { name: 'Cerebrium' })).not.toBeInTheDocument();
    window.dispatchEvent(new Event('pagehide'));
    await waitFor(() => expect(resolve).toHaveBeenCalledTimes(2));
    expect(resolve).toHaveBeenCalledWith(
      domainItem.id,
      { kind: 'edit', field: 'domain', value: 'cerebrium.example', source: 'Company website' },
      domainItem.revision,
      expect.anything(),
    );
    expect(resolve).toHaveBeenCalledWith(
      sizeItem.id,
      { kind: 'edit', field: 'size', value: 'about 30', source: 'Company website' },
      sizeItem.revision,
      expect.anything(),
    );
  });

  it('a field left empty stays open: only the filled one is saved', async () => {
    const user = userEvent.setup();
    const { client, domainItem } = await openTwoQuestions();
    const resolve = vi.spyOn(client, 'resolveReview');

    fireEvent.click(item('Cerebrium'));
    press('e');
    const row = item('Cerebrium');
    await user.type(await within(row).findByRole('textbox', { name: 'Email domain' }), 'cerebrium.example');
    await user.type(within(row).getByRole('textbox', { name: 'Source' }), 'Company website');
    await user.click(within(row).getByRole('button', { name: 'Save and lock' }));

    // The size question is still there, as a plain row.
    const left = await screen.findByRole('article', { name: 'Cerebrium' });
    expect(within(left).getByText('Unknown size')).toBeInTheDocument();
    expect(within(left).queryByText('Missing email domain')).not.toBeInTheDocument();
    window.dispatchEvent(new Event('pagehide'));
    await waitFor(() => expect(resolve).toHaveBeenCalledTimes(1));
    expect(resolve).toHaveBeenCalledWith(domainItem.id, expect.objectContaining({ field: 'domain' }), domainItem.revision, expect.anything());
  });

  it('a half-typed field blocks Save: a size with no digit is not enough', async () => {
    const user = userEvent.setup();
    await openTwoQuestions();
    fireEvent.click(item('Cerebrium'));
    press('e');
    const row = item('Cerebrium');
    await user.type(await within(row).findByRole('textbox', { name: 'Size' }), 'small');
    await user.type(within(row).getByRole('textbox', { name: 'Source' }), 'Company website');
    expect(within(row).getByRole('button', { name: 'Save and lock' })).toHaveAttribute('aria-disabled', 'true');
  });

  it('o leaves every question on the row as it is, in one click', async () => {
    const { client, domainItem, sizeItem } = await openTwoQuestions();
    const resolve = vi.spyOn(client, 'resolveReview');

    fireEvent.click(item('Cerebrium'));
    press('o');
    expect(screen.queryByRole('article', { name: 'Cerebrium' })).not.toBeInTheDocument();
    window.dispatchEvent(new Event('pagehide'));
    await waitFor(() => expect(resolve).toHaveBeenCalledTimes(2));
    expect(resolve).toHaveBeenCalledWith(domainItem.id, { kind: 'ok' }, domainItem.revision, expect.anything());
    expect(resolve).toHaveBeenCalledWith(sizeItem.id, { kind: 'ok' }, sizeItem.revision, expect.anything());
  });

  it('a company with one open question keeps its plain row', async () => {
    await openTwoQuestions();
    // Hex and Zed Industries have one question each, so they read as before.
    expect(within(item('Zed Industries')).getByRole('button', { name: /Edit board/ })).toBeInTheDocument();
    expect(within(item('Hex')).getByRole('button', { name: /Set sponsorship/ })).toBeInTheDocument();
  });
});
