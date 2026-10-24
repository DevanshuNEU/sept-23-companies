import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture, companyId, ids } from '@/api/fixture/data';
import { renderWithApp } from '@/test/render';
import OutboxPage from './index';

describe('company actions', () => {
  it('a close queued before a pause lands carries the pause\'s revision on pagehide', async () => {
    const client = new FixtureClient(buildFixture());
    renderWithApp(<OutboxPage />, { route: `/outbox/${ids.touch.felix}`, client });
    await screen.findByRole('heading', { level: 2, name: 'Felix Graham' });
    const graphite = companyId('Graphite');
    const before = (await client.company(graphite)).company.revision;

    // The pause is in flight (sent, not answered) while the close is queued.
    const realPause = client.pauseCompany.bind(client);
    let land!: () => void;
    const pause = vi.spyOn(client, 'pauseCompany').mockImplementation(
      (...args) => new Promise<void>((resolve, reject) => (land = () => void realPause(...args).then(resolve, reject))),
    );
    const close = vi.spyOn(client, 'closeCompany');
    fireEvent.keyDown(document.body, { key: 'p' });
    await waitFor(() => expect(pause).toHaveBeenCalledTimes(1));

    fireEvent.keyDown(document.body, { key: 'c' });
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('radio', { name: 'No referrals' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close company' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));

    // The pause lands, and the app reads the revision it made.
    await act(async () => land());
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    const after = (await client.company(graphite)).company.revision;
    expect(after).toBeGreaterThan(before);

    // The tab closes: a read would never come back, so the close goes out with what the app already knows.
    vi.spyOn(client, 'company').mockImplementation(() => new Promise<never>(() => {}));
    await act(async () => void window.dispatchEvent(new Event('pagehide')));
    expect(close).toHaveBeenCalledTimes(1);
    expect(close).toHaveBeenCalledWith(graphite, 'no_referrals', '', after, { requestId: expect.any(String), keepalive: true });
  });
});
