import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { composeClient } from '@/api/compose';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture } from '@/api/fixture/data';
import { createHttpClient } from '@/api/http/client';
import type { Transport } from '@/api/http/transport';
import { DEFAULT_LIVE_VIEWS } from '@/api/http/views';
import type { JobRunSummary, PlanView } from '@/api';
import { renderWithApp } from '@/test/render';
import TodayPage from './index';

const press = (key: string) => fireEvent.keyDown(document.body, { key });
const closeTab = () => act(async () => void window.dispatchEvent(new Event('pagehide')));

/** A fake transport: every read answers from `reads` by path, every write answers `{}`. Nothing reaches a server. */
function transport(reads: Record<string, unknown> = {}) {
  return {
    get: vi.fn(async (path: string) => {
      if (path in reads) return reads[path];
      throw new Error(`no read for ${path}`);
    }),
    send: vi.fn(async () => ({})),
  };
}

const EMPTY_DAY = {
  asOf: '2026-11-10T13:00:00Z', date: '2026-11-10', builtAt: null, rows: [], done: 0, total: 0,
  linkedinWeek: { used: 12, limit: 150, hardUsed: 14, hardLimit: 200 },
  tomorrow: { total: 7, text: 'Tomorrow: 7 people.', preview: [] },
  week: { requests: 0, accepts: 0, replies: 0 },
  inFlight: [],
  passed: 0,
  emailCount: 0,
  emailText: '',
} satisfies Omit<PlanView, 'listJob' | 'notReady'>;

const FAILED_JOB: JobRunSummary = {
  job: 'plan', status: 'failed', startedAt: '2026-11-10T13:00:00Z', finishedAt: '2026-11-10T13:00:05Z', error: 'no ranked companies',
  late: false, expectedNextAt: null, summary: '',
} as unknown as JobRunSummary;

describe('the plan page on the live API', () => {
  it('is live by default, with Today (Not a fit is a Today write)', () => {
    expect(DEFAULT_LIVE_VIEWS).toContain('plan');
    expect(DEFAULT_LIVE_VIEWS).toContain('today');
  });

  it('reads GET /api/plan and shows a day the plan job has not built yet as not built, with tomorrow', async () => {
    const t = transport({
      '/api/plan': { ...EMPTY_DAY, listJob: null, notReady: { reason: 'not_built', text: "Today's plan is not built yet. It builds at 8:00 on weekdays." } },
    });
    const client = composeClient({ live: createHttpClient(t as unknown as Transport), sample: null, views: DEFAULT_LIVE_VIEWS });
    renderWithApp(<TodayPage />, { route: '/today', client });
    expect(await screen.findByText("Today's plan is not built yet. It builds at 8:00 on weekdays.")).toBeInTheDocument();
    expect(screen.getByText('Tomorrow: 7 people.')).toBeInTheDocument();
    expect(t.get).toHaveBeenCalledWith('/api/plan', undefined);
    expect(screen.queryByRole('listbox', { name: "Today's people" })).not.toBeInTheDocument();
  });

  it('a failed plan job shows the job banner with its error and the API words for the day', async () => {
    const t = transport({
      '/api/plan': { ...EMPTY_DAY, listJob: FAILED_JOB, notReady: { reason: 'job_failed', text: "Today's plan did not build. The plan job said: no ranked companies" } },
    });
    const client = composeClient({ live: createHttpClient(t as unknown as Transport), sample: null, views: DEFAULT_LIVE_VIEWS });
    renderWithApp(<TodayPage />, { route: '/today', client });
    expect(await screen.findByText("Today's plan did not build. The plan job said: no ranked companies")).toBeInTheDocument();
    expect(screen.getByText('no ranked companies')).toBeInTheDocument();
  });
});

/**
 * The plan on sample data while Today is live. Its writes must then stay on the sample side: a fixture company id must
 * never reach the real store, whether it is a log or Not now.
 */
describe('the sample plan beside a live Today', () => {
  const VIEWS = DEFAULT_LIVE_VIEWS.filter((v) => v !== 'plan');

  async function open() {
    const t = transport();
    const sample = new FixtureClient(buildFixture());
    const client = composeClient({ live: createHttpClient(t as unknown as Transport), sample, views: VIEWS });
    renderWithApp(<TodayPage />, { route: '/today', client });
    await screen.findByRole('heading', { level: 2, name: 'Lena Ortiz' });
    return { t, sample };
  }

  it('runs with Today live and the plan on sample data', () => {
    expect(VIEWS).toContain('today');
    expect(VIEWS).not.toContain('plan');
  });

  it('Not now and a log never reach the live transport', async () => {
    const { t, sample } = await open();
    const passed = vi.spyOn(sample, 'passPick');
    const request = vi.spyOn(sample, 'logRequest');
    press('r');
    await screen.findByText('Logged: request sent to Lena Ortiz.');
    fireEvent.click(screen.getByRole('option', { name: /^4\. Jonah Park/ }));
    await screen.findByRole('heading', { level: 2, name: 'Jonah Park' });
    press('n');
    press('2');
    await screen.findByText('Passed. Added Retell AI.');
    await closeTab();
    await waitFor(() => expect(passed).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    expect(t.get).not.toHaveBeenCalled();
    expect(t.send).not.toHaveBeenCalled();
  });
});
