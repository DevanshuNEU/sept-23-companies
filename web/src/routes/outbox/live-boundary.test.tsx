import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { composeClient } from '@/api/compose';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture, ids } from '@/api/fixture/data';
import { createHttpClient } from '@/api/http/client';
import type { Transport } from '@/api/http/transport';
import { DEFAULT_LIVE_VIEWS } from '@/api/http/views';
import { renderWithApp } from '@/test/render';
import OutboxPage from './index';

/**
 * An Outbox on sample data while the company view is live. Its company actions must then stay on the sample side:
 * a fixture company id must never reach the real store. The Outbox is live by default now (with sending and the
 * shell), so this pins the views to the state the rule is about: the defaults without those three.
 */
const VIEWS = DEFAULT_LIVE_VIEWS.filter((v) => v !== 'outbox' && v !== 'sending' && v !== 'shell');

function boundary() {
  const transport = { get: vi.fn(async () => ({})), send: vi.fn(async () => ({})) };
  const sample = new FixtureClient(buildFixture());
  const client = composeClient({ live: createHttpClient(transport as unknown as Transport), sample, views: VIEWS });
  return { transport, sample, client };
}

async function open(touchId: number, name: string) {
  const b = boundary();
  renderWithApp(<OutboxPage />, { route: `/outbox/${touchId}`, client: b.client });
  await screen.findByRole('heading', { level: 2, name });
  return b;
}

const closeTab = () => act(async () => void window.dispatchEvent(new Event('pagehide')));

describe('the sample Outbox beside live views', () => {
  it('runs with the company view live and the Outbox on sample data', () => {
    expect(VIEWS).not.toContain('outbox');
    expect(VIEWS).toContain('company');
  });

  it('Pause and Close never reach the live transport', async () => {
    const { transport, sample } = await open(ids.touch.felix, 'Felix Graham');
    const pause = vi.spyOn(sample, 'pauseCompany');
    const close = vi.spyOn(sample, 'closeCompany');
    fireEvent.keyDown(document.body, { key: 'p' });
    await waitFor(() => expect(pause).toHaveBeenCalledTimes(1));
    fireEvent.keyDown(document.body, { key: 'c' });
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('radio', { name: 'No referrals' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close company' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await closeTab();
    expect(close).toHaveBeenCalledTimes(1);
    expect(transport.get).not.toHaveBeenCalled();
    expect(transport.send).not.toHaveBeenCalled();
  });

  it('Resume (a hold release here) never reaches the live transport', async () => {
    const { transport, sample } = await open(ids.touch.priya, 'Priya Natarajan');
    const release = vi.spyOn(sample, 'releaseHold');
    fireEvent.click(screen.getByRole('button', { name: 'Resume Browserbase' }));
    const dialog = await screen.findByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Resume Browserbase' }));
    await waitFor(() => expect(release).toHaveBeenCalledTimes(1));
    expect(transport.get).not.toHaveBeenCalled();
    expect(transport.send).not.toHaveBeenCalled();
  });

  it('the role kind never reaches the live transport', async () => {
    const { transport, sample } = await open(ids.touch.dana, 'Dana Whitcomb');
    const setRoleKind = vi.spyOn(sample, 'setRoleKind');
    act(() => screen.getByRole('combobox', { name: 'Role kind' }).focus());
    await userEvent.setup().keyboard('{Enter}');
    fireEvent.click(await screen.findByRole('option', { name: 'Engineer' }));
    await waitFor(() => expect(setRoleKind).toHaveBeenCalledTimes(1));
    expect(transport.get).not.toHaveBeenCalled();
    expect(transport.send).not.toHaveBeenCalled();
  });
});
