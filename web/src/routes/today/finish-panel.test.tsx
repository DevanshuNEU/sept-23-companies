import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { toast } from 'sonner';
import type { PlanRow, PlanView } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture, type FixtureDb, type Scenario } from '@/api/fixture/data';
import { formatClockTime } from '@/lib/format';
import { AppShell } from '@/shell/app-shell';
import { renderWithApp } from '@/test/render';
import { FinishPanel } from './finish-panel';
import TodayPage from './index';

const press = (key: string) => fireEvent.keyDown(document.body, { key });

const planOf = (scenario: Scenario = 'plan_done'): PlanView => buildFixture(scenario).plan;

/** The panel alone, inside the app providers (it announces through the shared live region). */
function showPanel(plan: PlanView) {
  return renderWithApp(<FinishPanel plan={plan} />);
}

async function openToday(o: { db?: FixtureDb; scenario?: Scenario; shell?: boolean } = {}) {
  const db = o.db ?? buildFixture(o.scenario ?? 'plan_done');
  const ui = o.shell ? (
    <>
      <AppShell />
      <TodayPage />
    </>
  ) : (
    <TodayPage />
  );
  const view = renderWithApp(ui, { client: new FixtureClient(db) });
  return { ...view, db };
}

const heading = () => screen.getByRole('heading', { level: 2, name: 'Done for today' });
const section = (name: string) => screen.getByRole('region', { name });
const rail = () => within(screen.getByRole('listbox', { name: "Today's people" })).getAllByRole('option');

/** Carries a row to tomorrow in the data, the way the API reports it. */
function carry(plan: PlanView, position: number) {
  const row = plan.rows[position - 1];
  if (!row) throw new Error(`No row ${position}`);
  row.state = 'skipped';
  row.steps = row.steps.map((s) => ({ ...s, at: null, touchId: null }));
  plan.done = plan.rows.filter((r) => r.state === 'done').length;
}

/** Removes a row's company as not a fit, the way the API reports it: the row is off the plan and out of the total. */
function takeOff(plan: PlanView, position: number) {
  plan.rows.splice(position - 1, 1);
  plan.total = plan.rows.length;
  plan.done = plan.rows.filter((r) => r.state === 'done').length;
}

afterEach(() => {
  toast.dismiss();
  window.localStorage.clear();
});

describe('FinishPanel', () => {
  it('shows what he did from the rows', () => {
    const plan = planOf();
    showPanel(plan);
    const did = within(section('What you did'));
    const items = did.getAllByRole('listitem');
    const ledger = items.slice(0, 3).map((li) => li.textContent);
    expect(ledger).toEqual(['6requests sent', '6applications sent', '1email reviewed']);

    // One line per row done: the person, the company, the step and the time with no leading zero.
    const dana = plan.rows[0] as PlanRow;
    const last = dana.steps.flatMap((s) => (s.at ? [s.at] : [])).sort().at(-1);
    if (!last) throw new Error('fixture: Dana has logged steps');
    const line = did.getByText('Dana Whitfield').closest('li');
    expect(line).toHaveTextContent('Dana Whitfield at Resend');
    expect(line).toHaveTextContent('request and application');
    expect(line).toHaveTextContent(formatClockTime(last));
    expect(line?.textContent).not.toMatch(/\b0\d:\d\d/);
    expect(did.getByText('Mintlify').closest('li')).toHaveTextContent('reviewed in the Outbox');
    expect(did.getAllByRole('listitem')).toHaveLength(3 + 7);
  });

  it('says what the day was in the heading and the line under it, calm and with no guilt', () => {
    showPanel(planOf());
    expect(heading()).toHaveAttribute('tabindex', '-1');
    expect(screen.getByText("All 7 people on today's plan are done. Nothing else is asked of you today.")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/streak|come back|overdue|behind|missed|keep it up/i);
  });

  it('previews tomorrow with at most 3 lines and "and 4 more"', () => {
    const plan = planOf();
    expect(plan.tomorrow.preview.length).toBeGreaterThan(3);
    expect(plan.tomorrow.total).toBe(7);
    showPanel(plan);
    const tomorrow = within(section('Tomorrow'));
    expect(tomorrow.getByText('Tomorrow: 7 people, 5 carried over.')).toBeInTheDocument();
    const lines = tomorrow.getAllByRole('listitem').map((li) => li.textContent);
    expect(lines).toEqual(['1Aiden Frost at Hex, Software Engineer, New Grad', '2Rhea Kapoor at Warp, Software Engineer I', '3Find someone at Clerk, Product Engineer']);
    expect(tomorrow.getByText('and 4 more')).toBeInTheDocument();
  });

  it('names no "and more" line when the preview shows everyone', () => {
    const plan = planOf();
    plan.tomorrow = { ...plan.tomorrow, total: 2, text: 'Tomorrow: 2 people, 2 carried over.', preview: plan.tomorrow.preview.slice(0, 2) };
    showPanel(plan);
    expect(within(section('Tomorrow')).getAllByRole('listitem')).toHaveLength(2);
    expect(screen.queryByText(/and \d+ more/)).not.toBeInTheDocument();
  });

  it('shows no Tomorrow list when nothing is planned', () => {
    const plan = planOf();
    plan.tomorrow = { total: 0, text: 'Nothing is planned yet.', preview: [] };
    showPanel(plan);
    const tomorrow = within(section('Tomorrow'));
    expect(tomorrow.getByText('Nothing is planned yet.')).toBeInTheDocument();
    expect(tomorrow.queryByRole('listitem')).not.toBeInTheDocument();
  });

  it('shows no Carried to tomorrow section when nothing is skipped', () => {
    showPanel(planOf());
    expect(screen.queryByRole('region', { name: 'Carried to tomorrow' })).not.toBeInTheDocument();
  });

  it('skipped rows are listed under Carried to tomorrow, and the counts say so', () => {
    const plan = planOf();
    carry(plan, 6);
    showPanel(plan);
    const carried = within(section('Carried to tomorrow'));
    expect(carried.getByText('Ines Duarte')).toBeInTheDocument();
    expect(carried.getByText(/Turso/)).toBeInTheDocument();
    expect(screen.getByText('6 of 7 done. Ines Duarte carries to tomorrow, so nothing else is asked of you today.')).toBeInTheDocument();
    // What he did leaves her out: no step was logged.
    expect(within(section('What you did')).queryByText('Ines Duarte')).not.toBeInTheDocument();
    expect(within(section('What you did')).getByText('requests sent').previousSibling).toHaveTextContent('5');
  });

  it('two carried rows read as a count', () => {
    const plan = planOf();
    carry(plan, 5);
    carry(plan, 6);
    showPanel(plan);
    expect(within(section('Carried to tomorrow')).getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByText('5 of 7 done. 2 carry to tomorrow, so nothing else is asked of you today.')).toBeInTheDocument();
  });

  it('a company removed as not a fit is off the plan: it is neither carried nor listed as done', () => {
    const plan = planOf();
    takeOff(plan, 6);
    showPanel(plan);
    expect(screen.queryByRole('region', { name: 'Carried to tomorrow' })).not.toBeInTheDocument();
    expect(screen.queryByText(/carries? to tomorrow/)).not.toBeInTheDocument();
    expect(screen.getByText("All 6 people on today's plan are done. Nothing else is asked of you today.")).toBeInTheDocument();
    expect(within(section('What you did')).queryByText('Ines Duarte')).not.toBeInTheDocument();
  });

  it('the week line links to /progress', () => {
    const plan = planOf();
    showPanel(plan);
    const week = within(section('This week so far'));
    expect(week.getByText('35 requests, 9 accepted, 2 replies.')).toBeInTheDocument();
    expect(week.getByRole('link', { name: 'Open Progress' })).toHaveAttribute('href', '/progress');
  });

  it('writes one request and one reply in the singular', () => {
    const plan = planOf();
    plan.week = { requests: 1, accepts: 0, replies: 1 };
    showPanel(plan);
    expect(screen.getByText('1 request, 0 accepted, 1 reply.')).toBeInTheDocument();
  });

  it('does not take focus from a field he is typing in', async () => {
    const field = document.createElement('textarea');
    document.body.append(field);
    field.focus();
    try {
      showPanel(planOf());
      await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('All 7 done for today.'));
      expect(field).toHaveFocus();
    } finally {
      field.remove();
    }
  });

  it('announces the day and takes focus on arrival', async () => {
    showPanel(planOf());
    await waitFor(() => expect(heading()).toHaveFocus());
    expect(screen.getByRole('status')).toHaveTextContent('All 7 done for today.');
  });

  it('writes each ledger label in the singular for one', () => {
    const plan = planOf();
    // One request, one application and one email reviewed: only row 1 and the email row stay done.
    for (const r of plan.rows) {
      if (r.channel === 'linkedin_request' && r.position !== 1) {
        r.state = 'skipped';
        r.steps = r.steps.map((s) => ({ ...s, at: null, touchId: null }));
      }
    }
    showPanel(plan);
    const ledger = within(section('What you did'))
      .getAllByRole('listitem')
      .slice(0, 3)
      .map((li) => li.textContent);
    expect(ledger).toEqual(['1request sent', '1application sent', '1email reviewed']);
  });

  it('takes the latest step time by instant, not by text', () => {
    const plan = planOf();
    const dana = plan.rows[0] as PlanRow;
    // The same instant order written with different offsets: as text "...T09:30:00-05:00" sorts before "...T14:10:00Z".
    const [first, second] = dana.steps;
    if (!first || !second) throw new Error('fixture: Dana has two steps');
    first.at = '2026-11-10T14:10:00Z';
    second.at = '2026-11-10T09:30:00-05:00';
    showPanel(plan);
    expect(within(section('What you did')).getByText('Dana Whitfield').closest('li')).toHaveTextContent(formatClockTime('2026-11-10T14:30:00Z'));
  });

  it('announces what carried over', async () => {
    const plan = planOf();
    carry(plan, 6);
    showPanel(plan);
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Done for today. 6 of 7 done, 1 carried to tomorrow.'));
  });
});

describe('Today with no row to do', () => {
  it('shows the finish panel in the work region, with the rail ending on its finish node', async () => {
    await openToday();
    expect(await screen.findByRole('heading', { level: 2, name: 'Done for today' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Finish' })).toBeInTheDocument();
    expect(screen.getByText('7 people, 7 done')).toBeInTheDocument();
    expect(document.querySelector('[data-finish]')).toHaveAttribute('data-done', 'true');
    expect(rail().every((o) => o.getAttribute('aria-selected') === 'false')).toBe(true);
  });

  it('a carried row shows in the head count and the rail, never red', async () => {
    const db = buildFixture('plan_done');
    carry(db.plan, 6);
    await openToday({ db });
    expect(await screen.findByRole('heading', { level: 2, name: 'Done for today' })).toBeInTheDocument();
    expect(screen.getByText('7 people, 6 done, 1 carried over')).toBeInTheDocument();
    expect(rail()[5]).toHaveAccessibleName('6. Ines Duarte at Turso, Carried over');
  });

  it('picking a person on the rail shows their panel, and the heading stays out of it', async () => {
    await openToday();
    await screen.findByRole('heading', { level: 2, name: 'Done for today' });
    fireEvent.click(rail()[0] as HTMLElement);
    expect(await screen.findByRole('heading', { level: 2, name: 'Dana Whitfield' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { level: 2, name: 'Done for today' })).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Selected person' })).toBeInTheDocument();
  });

  it('focus moves to the finish heading when the last step is logged', async () => {
    const db = buildFixture();
    // Rows 5 (find someone) and 7 (email) have no key, so they are done; the rest are logged by keys.
    for (const n of [5, 7]) (db.plan.rows[n - 1] as PlanRow).state = 'done';
    await openToday({ db, shell: true });
    expect(await screen.findByRole('heading', { level: 2, name: 'Lena Ortiz' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Done for today' })).not.toBeInTheDocument();

    press('r');
    press('p');
    expect(await screen.findByRole('heading', { level: 2, name: 'Jonah Park' })).toBeInTheDocument();
    press('r');
    press('p');
    expect(await screen.findByRole('heading', { level: 2, name: 'Ines Duarte' })).toBeInTheDocument();
    press('r');
    press('p');

    await waitFor(() => expect(heading()).toHaveFocus());
    expect(screen.getAllByRole('status').some((s) => s.textContent === 'All 7 done for today.')).toBe(true);
  });

  it('appears at once, with no aria-busy and no waiting for the poll', async () => {
    const db = buildFixture();
    for (const n of [4, 5, 6, 7]) (db.plan.rows[n - 1] as PlanRow).state = 'done';
    await openToday({ db });
    expect(await screen.findByRole('heading', { level: 2, name: 'Lena Ortiz' })).toBeInTheDocument();
    // Row 3 is the only one to do. Its request and apply logs finish the day inside one tick.
    act(() => {
      press('r');
      press('p');
    });
    expect(screen.getByRole('heading', { level: 2, name: 'Done for today' })).toBeInTheDocument();
    expect(document.querySelector('[aria-busy="true"]')).toBeNull();
  });

  it('a company removed as not a fit leaves the head carried-over count at zero', async () => {
    const db = buildFixture('plan_done');
    takeOff(db.plan, 6);
    await openToday({ db });
    expect(await screen.findByRole('heading', { level: 2, name: 'Done for today' })).toBeInTheDocument();
    expect(screen.getByText('6 people, 6 done')).toBeInTheDocument();
    expect(screen.queryByText(/carried to tomorrow/)).not.toBeInTheDocument();
  });

  it('the weekend plan (no rows) shows the not-ready state, not the finish panel', async () => {
    await openToday({ scenario: 'weekend' });
    expect(await screen.findByText(/No plan on weekends/)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Done for today' })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Finish' })).not.toBeInTheDocument();
  });
});
