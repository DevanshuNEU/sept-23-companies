import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router';
import type { CompanyView, Id } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { companyId } from '@/api/fixture/data';
import { renderWithApp } from '@/test/render';
import CompanyPage from './index';

const BROWSERBASE = companyId('Browserbase');

function Where() {
  const location = useLocation();
  return <p data-testid="where">{location.pathname + location.search}</p>;
}

const press = (key: string) => fireEvent.keyDown(document.body, { key });
const where = () => screen.getByTestId('where').textContent;
const fact = (name: string) => screen.getByRole('group', { name });
const panel = () => screen.getByRole('tabpanel');

async function open(path = `/companies/${BROWSERBASE}`, client: FixtureClient = new FixtureClient()) {
  const view = renderWithApp(
    <>
      <CompanyPage />
      <Where />
    </>,
    { route: path, client },
  );
  await screen.findByRole('region', { name: 'Facts' });
  return view;
}

describe('Company page', () => {
  it('shows the name, the status badges and where it stands', async () => {
    await open();
    expect(screen.getByRole('heading', { level: 1, name: 'Browserbase' })).toBeInTheDocument();
    const status = screen.getByRole('group', { name: 'Status' });
    expect(within(status).getByText('Held: reply')).toBeInTheDocument();
    expect(within(status).getByText('Day 3 of 3')).toBeInTheDocument();
    expect(screen.getByText(/Priya Natarajan replied today at 9:12, so 2 queued emails are held\./)).toBeInTheDocument();
  });

  it('shows the facts strip: size with confidence, sponsorship, domain, board and E-Verify', async () => {
    await open();
    expect(within(fact('Size')).getByText('48')).toBeInTheDocument();
    expect(within(fact('Size')).getByRole('img', { name: 'High confidence' })).toBeInTheDocument();
    expect(within(fact('Sponsorship')).getByText('Says it sponsors')).toBeInTheDocument();
    expect(within(fact('Stage')).getByText('Series B')).toBeInTheDocument();
    expect(within(fact('Email domain')).getByText('browserbase.example')).toBeInTheDocument();
    expect(within(fact('Email domain')).getByRole('img', { name: 'Locked' })).toBeInTheDocument();
    expect(within(fact('Email domain')).getByText('Mail server found')).toBeInTheDocument();
    expect(within(fact('Job board')).getByText('Ashby, ok')).toBeInTheDocument();
    expect(within(fact('Job board')).getByText('Read Mon Nov 9, 08:04')).toBeInTheDocument();
    expect(within(fact('E-Verify')).getByText('Participates in E-Verify')).toBeInTheDocument();
  });

  it('leaves E-Verify out when the company has no everify fact', async () => {
    await open(`/companies/${companyId('Mintlify')}`);
    expect(screen.getByRole('heading', { level: 1, name: 'Mintlify' })).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'E-Verify' })).not.toBeInTheDocument();
  });

  it('3 opens People and puts the tab in the URL', async () => {
    await open();
    expect(screen.getByRole('tab', { name: /^Overview/ })).toHaveAttribute('aria-selected', 'true');
    act(() => {
      press('3');
    });
    expect(screen.getByRole('tab', { name: /^People/ })).toHaveAttribute('aria-selected', 'true');
    expect(where()).toBe(`/companies/${BROWSERBASE}?tab=people`);
    const people = panel();
    expect(within(people).getByRole('columnheader', { name: 'Channel' })).toBeInTheDocument();
    expect(within(people).getByText('Owen Clarke')).toBeInTheDocument();
    expect(within(people).getByRole('link', { name: 'Open in Outbox' })).toHaveAttribute('href', '/outbox/2001');

    act(() => {
      press('1');
    });
    expect(where()).toBe(`/companies/${BROWSERBASE}`);
    expect(within(panel()).getByText('Where it stands')).toBeInTheDocument();
  });

  it('History shows every event with its source tag, imports included', async () => {
    await open(`/companies/${BROWSERBASE}?tab=history`);
    const history = panel();
    for (const tag of ['You', 'Claude', 'Sender', 'Crawler', 'Rules', 'Old dashboard', 'contacted.txt', 'Old devboard', 'Job Radar']) {
      expect(within(history).getAllByText(tag, { selector: '[data-slot="badge"]' }).length).toBeGreaterThan(0);
    }
    expect(within(history).getByText('Lena Szabo messaged on LinkedIn')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /^History/ })).toHaveTextContent('31');
  });

  it('a tab that fails shows the partial state while the others render', async () => {
    // A posting the page cannot read: its Roles tab fails, the header and the other tabs still work.
    class BrokenPosting extends FixtureClient {
      override async company(id: Id): Promise<CompanyView> {
        const v = await super.company(id);
        return { ...v, postings: v.postings.map((p, i) => (i === 0 ? { ...p, firstSeenAt: 'not a time' } : p)) };
      }
    }
    const logged = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      await open(`/companies/${BROWSERBASE}?tab=roles`, new BrokenPosting());
      expect(within(panel()).getByText('Roles did not load for this company.')).toBeInTheDocument();
      expect(within(panel()).getByRole('button', { name: 'Retry' })).toBeInTheDocument();
      expect(screen.getByRole('heading', { level: 1, name: 'Browserbase' })).toBeInTheDocument();
      expect(within(fact('Size')).getByText('48')).toBeInTheDocument();

      act(() => {
        press('5');
      });
      expect(within(panel()).getByText('"We sponsor visas for this role."')).toBeInTheDocument();
      expect(logged).toHaveBeenCalled();
    } finally {
      logged.mockRestore();
    }
  });

  it('a posting address that is not http or https shows the title as plain text', async () => {
    class Unsafe extends FixtureClient {
      override async company(id: Id): Promise<CompanyView> {
        const v = await super.company(id);
        return { ...v, postings: v.postings.map((p, i) => (i === 0 ? { ...p, url: 'javascript:alert(1)' } : p)) };
      }
    }
    await open(`/companies/${BROWSERBASE}?tab=roles`, new Unsafe());
    const title = (await screen.findByText('Software Engineer, New Grad', { selector: 'td *, td' }));
    expect(title.closest('a')).toBeNull();
    expect(document.querySelector('a[href^="javascript:"]')).toBeNull();
  });

  it('C closes with a reason as an undoable write', async () => {
    const { client } = await open();
    const close = vi.spyOn(client, 'closeCompany');
    act(() => {
      press('c');
    });
    const dialog = await screen.findByRole('dialog', { name: 'Close Browserbase' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close company' }));

    expect(await screen.findByText('Closed Browserbase: interview. It is on the Done list.')).toBeInTheDocument();
    expect(within(screen.getByRole('group', { name: 'Status' })).getByText('Done')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(within(screen.getByRole('group', { name: 'Status' })).getByText('Held: reply')).toBeInTheDocument());
    expect(close).not.toHaveBeenCalled();
  });

  it('P pauses at once: the API is called with no wait and no Undo', async () => {
    const { client } = await open();
    const pause = vi.spyOn(client, 'pauseCompany');
    act(() => {
      press('p');
    });
    expect(await screen.findByText('Paused Browserbase. Nothing more sends there until you resume.')).toBeInTheDocument();
    expect(within(screen.getByRole('group', { name: 'Status' })).getByText('Paused')).toBeInTheDocument();
    await waitFor(() => expect(pause).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull();
  });

  it('Pause, then Close inside the window: the close reads the new revision and does not conflict', async () => {
    const { client } = await open();
    const pause = vi.spyOn(client, 'pauseCompany');
    const close = vi.spyOn(client, 'closeCompany');
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'requestAnimationFrame', 'cancelAnimationFrame'] });
    try {
      act(() => {
        press('p');
      });
      await act(() => vi.advanceTimersByTimeAsync(1000));
      expect(pause).toHaveBeenCalledTimes(1);
      act(() => {
        press('c');
      });
      await act(() => vi.advanceTimersByTimeAsync(0));
      const dialog = screen.getByRole('dialog', { name: 'Close Browserbase' });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Close company' }));
      await act(() => vi.advanceTimersByTimeAsync(8000));
      expect(close).toHaveBeenCalledTimes(1);
      expect(close.mock.calls[0]?.[3]).toBe(2);
      await act(() => vi.advanceTimersByTimeAsync(1000));
      expect(screen.queryByText(/changed since you loaded it/)).toBeNull();
      expect(within(screen.getByRole('group', { name: 'Status' })).getByText('Done')).toBeInTheDocument();
    } finally {
      await act(() => vi.runOnlyPendingTimersAsync());
      vi.useRealTimers();
    }
  });

  it('a close waiting in its window reads the revision when it commits, so another write in between does not conflict', async () => {
    const { client } = await open();
    const close = vi.spyOn(client, 'closeCompany');
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'requestAnimationFrame', 'cancelAnimationFrame'] });
    try {
      act(() => {
        press('c');
      });
      await act(() => vi.advanceTimersByTimeAsync(0));
      fireEvent.click(within(screen.getByRole('dialog', { name: 'Close Browserbase' })).getByRole('button', { name: 'Close company' }));
      await act(() => vi.advanceTimersByTimeAsync(1000));
      // Something else (Claude, another tab) moves the company's revision while the close waits.
      await act(async () => {
        await client.pauseCompany(BROWSERBASE, 1, { requestId: 'elsewhere' });
      });
      await act(() => vi.advanceTimersByTimeAsync(8000));
      expect(close).toHaveBeenCalledTimes(1);
      expect(close.mock.calls[0]?.[3]).toBe(2);
      await act(() => vi.advanceTimersByTimeAsync(1000));
      expect(screen.queryByText(/changed since you loaded it/)).toBeNull();
    } finally {
      await act(() => vi.runOnlyPendingTimersAsync());
      vi.useRealTimers();
    }
  });

  it('R asks first, listing the held emails, then resumes', async () => {
    const { client } = await open();
    const resume = vi.spyOn(client, 'resumeCompany');
    act(() => {
      press('r');
    });
    const dialog = await screen.findByRole('alertdialog', { name: 'Resume Browserbase' });
    expect(await within(dialog).findByText('Lena Szabo, first email (Approved)')).toBeInTheDocument();
    expect(within(dialog).getByText('Tom Becker, follow-up 1 (Draft)')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Resume company' }));
    await waitFor(() => expect(resume).toHaveBeenCalledTimes(1));
    expect(resume.mock.calls[0]?.slice(0, 2)).toEqual([BROWSERBASE, 1]);
  });

  it('a done company shows Done and reopens from the More menu after asking', async () => {
    const { client } = await open(`/companies/${companyId('Northbeam Labs')}`);
    const status = screen.getByRole('group', { name: 'Status' });
    expect(within(status).getByText('Done')).toBeInTheDocument();
    expect(within(status).getByText('Referral')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Close with a reason/ })).not.toBeInTheDocument();
    const reopen = vi.spyOn(client, 'reopenCompany');

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'More actions' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Reopen' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Reopen Northbeam Labs' });
    expect(reopen).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole('button', { name: 'Reopen company' }));

    expect(await screen.findByText('Reopened Northbeam Labs.')).toBeInTheDocument();
    await waitFor(() => expect(within(screen.getByRole('group', { name: 'Status' })).getByText('Emailing')).toBeInTheDocument());
    expect(reopen).toHaveBeenCalledTimes(1);
  });

  it('Y copies the role title', async () => {
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    try {
      await open();
      act(() => {
        press('y');
      });
      expect(writeText).toHaveBeenCalledWith('Software Engineer, New Grad');
      expect(await screen.findByText('Copied "Software Engineer, New Grad"')).toBeInTheDocument();
    } finally {
      Reflect.deleteProperty(navigator, 'clipboard');
    }
  });

  it('an unknown company says so', async () => {
    renderWithApp(<CompanyPage />, { route: '/companies/1' });
    expect(await screen.findByRole('heading', { level: 1, name: 'Company' })).toBeInTheDocument();
    expect(screen.getByText('No company with this id')).toBeInTheDocument();
  });
});
