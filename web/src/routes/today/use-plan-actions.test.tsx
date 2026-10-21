import { useEffect } from 'react';
import { act, fireEvent, screen } from '@testing-library/react';
import { ApiError, queryKeys, useClient, type Id, type PlanRow, type PromptTouch } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture, type Scenario } from '@/api/fixture/data';
import { usePendingControls, useViewQuery } from '@/pending/pending';
import { renderWithApp } from '@/test/render';
import { withStepLogged } from './plan-steps';
import { usePlanActions } from './use-plan-actions';

type Api = ReturnType<typeof usePlanActions> & { flush: () => Promise<void> };
const api: { current?: Api } = {};

function Harness() {
  const client = useClient();
  const actions = usePlanActions();
  const { flushPending } = usePendingControls();
  const plan = useViewQuery(queryKeys.plan, () => client.plan());
  const shell = useViewQuery(queryKeys.shell, () => client.shell());
  const prompt = useViewQuery(queryKeys.outcomePrompt, () => client.outcomePrompt());
  const wins = useViewQuery(queryKeys.wins, () => client.wins());
  useEffect(() => {
    api.current = { ...actions, flush: flushPending };
  });
  const row = (n: number) => plan.data?.rows[n - 1];
  return (
    <>
      <p data-testid="done">{plan.data ? String(plan.data.done) : 'loading'}</p>
      <p data-testid="shell-done">{shell.data ? String(shell.data.plan.done) : 'loading'}</p>
      <p data-testid="row3">{row(3)?.state}</p>
      <p data-testid="row3-steps">{row(3)?.steps.map((s) => (s.at ? 'x' : '-')).join('')}</p>
      <p data-testid="prompt">{prompt.data ? String(prompt.data.touches.length) : 'loading'}</p>
      <p data-testid="unseen">{wins.data ? String(wins.data.unseen) : 'loading'}</p>
    </>
  );
}

async function settle(ms = 0) {
  await act(() => vi.advanceTimersByTimeAsync(ms));
}

const text = (id: string) => screen.getByTestId(id).textContent;

async function open(scenario: Scenario = 'default') {
  const client = new FixtureClient(buildFixture(scenario));
  const plan = await client.plan();
  const prompt = await client.outcomePrompt();
  renderWithApp(<Harness />, { route: '/today', client });
  await settle();
  const act_ = <T,>(fn: (a: Api) => T): T => {
    let out: T | undefined;
    act(() => {
      out = fn(api.current as Api);
    });
    return out as T;
  };
  const rowOf = (n: number): PlanRow => {
    const row = plan.rows[n - 1];
    if (!row) throw new Error(`No row ${n}`);
    return row;
  };
  return { client, plan, prompt, rowOf, act: act_ };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(async () => {
  await act(async () => {
    await vi.runOnlyPendingTimersAsync();
  });
  vi.useRealTimers();
  vi.restoreAllMocks();
  delete api.current;
});

const REQUEST_TOAST = 'Logged: request sent to Lena Ortiz.';

describe('usePlanActions', () => {
  it('logRequest calls the client once after the window with key plan:<entryId>:request', async () => {
    const { client, rowOf, act: run } = await open();
    const spy = vi.spyOn(client, 'logRequest');
    const row = rowOf(3);
    run((a) => a.logRequest(row));
    await settle(7999);
    expect(spy).not.toHaveBeenCalled();
    await settle(1);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith(
      row.entryId,
      { personId: row.person?.person.id, idempotencyKey: `plan:${row.entryId}:request` },
      row.revision,
      { requestId: expect.any(String) },
    );
  });

  it('shows the logged toast with Undo and an afterCommit line that points to the company page', async () => {
    const { rowOf, act: run } = await open();
    run((a) => a.logRequest(rowOf(3)));
    await settle();
    expect(screen.getByText(REQUEST_TOAST)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();
    await act(() => api.current!.flush());
    await settle();
    expect(screen.getByText(`${REQUEST_TOAST} To change it, open Pylon's page.`)).toBeInTheDocument();
  });

  it('logApplied toasts the role and company and uses key plan:<entryId>:apply', async () => {
    const { client, rowOf, act: run } = await open();
    const spy = vi.spyOn(client, 'logApplied');
    const row = rowOf(3);
    run((a) => a.logApplied(row));
    await settle();
    expect(screen.getByText('Logged: applied for Software Engineer I at Pylon.')).toBeInTheDocument();
    await settle(8000);
    expect(spy).toHaveBeenCalledWith(
      row.entryId,
      { postingId: row.role?.postingId, idempotencyKey: `plan:${row.entryId}:apply` },
      row.revision,
      { requestId: expect.any(String) },
    );
  });

  it('two presses inside the window queue one write', async () => {
    const { client, rowOf, act: run } = await open();
    const spy = vi.spyOn(client, 'logRequest');
    const row = rowOf(3);
    run((a) => a.logRequest(row));
    await settle(1000);
    run((a) => a.logRequest(row));
    run((a) => a.logRequest(rowOf(3)));
    await settle();
    expect(screen.getAllByText(REQUEST_TOAST)).toHaveLength(1);
    expect(screen.queryByText(/Already logged/)).not.toBeInTheDocument();
    await settle(8000);
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('a press after the commit reaches the client with the same key and the fixture logs one touch', async () => {
    const { client, rowOf, act: run } = await open();
    const spy = vi.spyOn(client, 'logRequest');
    const row = rowOf(3);
    run((a) => a.logRequest(row));
    await settle(8000);
    await settle(50);
    const touch = (await client.plan()).rows[2]?.steps[0]?.touchId;
    const before = (await client.progress()).today.requests;

    // The same stale row again, as a second tab or a late double press would send it.
    run((a) => a.logRequest(row));
    await settle(8000);
    await settle(50);
    expect(spy).toHaveBeenCalledTimes(2);
    expect(spy.mock.calls[1]?.[1].idempotencyKey).toBe(spy.mock.calls[0]?.[1].idempotencyKey);
    const after = await client.plan();
    expect(after.rows[2]?.steps[0]?.touchId).toBe(touch);
    expect((await client.progress()).today.requests).toBe(before);
  });

  it('the next write uses the revision the previous write returned', async () => {
    const { client, rowOf, act: run } = await open();
    const request = vi.spyOn(client, 'logRequest');
    const apply = vi.spyOn(client, 'logApplied');
    const row = rowOf(3);
    run((a) => a.logRequest(row));
    await settle(8000);
    await settle(50);
    const returned = (await request.mock.results[0]?.value) as PlanRow;
    expect(returned.revision).toBe(row.revision + 1);

    run((a) => a.logApplied(row)); // the stale row: its revision is the old one
    await settle(8000);
    await settle(50);
    expect(apply.mock.calls[0]?.[2]).toBe(returned.revision);
    expect(text('row3')).toBe('done');
  });

  it('two steps flushed together are sent one after the other with the revision returned', async () => {
    const { client, rowOf, act: run } = await open();
    const request = vi.spyOn(client, 'logRequest');
    const apply = vi.spyOn(client, 'logApplied');
    const row = rowOf(3);
    run((a) => a.logRequest(row));
    run((a) => a.logApplied(row));
    await settle();
    await act(() => api.current!.flush());
    await settle(50);
    expect(request.mock.calls[0]?.[2]).toBe(row.revision);
    expect(apply.mock.calls[0]?.[2]).toBe(row.revision + 1);
    expect((await client.plan()).rows[2]?.state).toBe('done');
  });

  it('a flush as the tab closes sends both steps at once with the revision on screen, and both land in any order', async () => {
    const { client, rowOf, act: run } = await open();
    const request = vi.spyOn(client, 'logRequest');
    const original = client.logApplied.bind(client);
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    // The apply log is held back, so the request log reaches the server first and the apply log arrives second.
    const apply = vi.spyOn(client, 'logApplied').mockImplementation(async (...args) => {
      await gate;
      return original(...args);
    });
    const row = rowOf(3);
    run((a) => a.logApplied(row));
    run((a) => a.logRequest(row));
    await settle();
    act(() => {
      window.dispatchEvent(new Event('pagehide'));
    });
    expect(request).toHaveBeenCalledTimes(1);
    expect(apply).toHaveBeenCalledTimes(1);
    expect(request.mock.calls[0]?.[3]).toMatchObject({ keepalive: true });
    expect(apply.mock.calls[0]?.[3]).toMatchObject({ keepalive: true });
    // Step logs are append-only: neither carries a guessed revision, just the one the row had.
    expect(request.mock.calls[0]?.[2]).toBe(row.revision);
    expect(apply.mock.calls[0]?.[2]).toBe(row.revision);
    await settle(50);
    expect((await client.plan()).rows[2]?.steps.map((s) => s.at !== null)).toEqual([true, false]);
    release();
    await settle(50);
    expect((await client.plan()).rows[2]?.state).toBe('done');
  });

  it('a press on a logged step shows "Already logged" and calls nothing', async () => {
    const { client, plan, rowOf, act: run } = await open();
    const request = vi.spyOn(client, 'logRequest');
    const apply = vi.spyOn(client, 'logApplied');
    const logged = withStepLogged(plan, rowOf(3).entryId, 'request', plan.asOf).rows[2] as PlanRow;
    run((a) => a.logRequest(logged));
    await settle();
    expect(screen.getByText('Already logged.')).toBeInTheDocument();
    run((a) => a.logApplied(rowOf(1)));
    await settle(10000);
    expect(request).not.toHaveBeenCalled();
    expect(apply).not.toHaveBeenCalled();
  });

  it('logApplied on a row with no role toasts "No role to apply for." and calls nothing', async () => {
    const { client, rowOf, act: run } = await open();
    const apply = vi.spyOn(client, 'logApplied');
    const noRole: PlanRow = { ...rowOf(3), role: null, applyUrl: null, steps: rowOf(3).steps.filter((s) => s.kind === 'request') };
    run((a) => a.logApplied(noRole));
    await settle();
    expect(screen.getByText('No role to apply for.')).toBeInTheDocument();
    await settle(10000);
    expect(apply).not.toHaveBeenCalled();
  });

  it('logRequest on a row with nobody on it or an email row says so and calls nothing', async () => {
    const { client, rowOf, act: run } = await open();
    const request = vi.spyOn(client, 'logRequest');
    run((a) => a.logRequest(rowOf(5)));
    await settle();
    expect(screen.getByText('Find someone at Cerebrium first.')).toBeInTheDocument();
    run((a) => a.logRequest(rowOf(7)));
    await settle();
    expect(screen.getByText('Review this email in the Outbox.')).toBeInTheDocument();
    await settle(10000);
    expect(request).not.toHaveBeenCalled();
  });

  it('undo inside the window sends nothing and the row is todo again', async () => {
    const { client, rowOf, act: run } = await open();
    const spy = vi.spyOn(client, 'logRequest');
    run((a) => a.logRequest(rowOf(3)));
    await settle();
    expect(text('row3-steps')).toBe('x-');
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    await settle(10000);
    expect(spy).not.toHaveBeenCalled();
    expect(text('row3')).toBe('todo');
    expect(text('row3-steps')).toBe('--');
    expect(text('done')).toBe('2');
  });

  it('leaving the view flushes the write', async () => {
    const { client, rowOf, act: run } = await open();
    const spy = vi.spyOn(client, 'logRequest');
    run((a) => a.logRequest(rowOf(3)));
    await settle(1000);
    expect(spy).not.toHaveBeenCalled();
    await act(() => api.current!.flush());
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('done count moves at once: the cached plan and the shell read 3 before any timer runs', async () => {
    const { client, plan, rowOf, act: run } = await open();
    const spy = vi.spyOn(client, 'logRequest');
    expect(text('done')).toBe('2');
    expect(text('shell-done')).toBe('2');
    const row = rowOf(3);
    run((a) => a.logRequest(row));
    await settle();
    expect(text('done')).toBe('2');
    expect(text('shell-done')).toBe('2');
    // The row as the screen shows it now, with the request already laid over it.
    const shown = withStepLogged(plan, row.entryId, 'request', plan.asOf).rows[2] as PlanRow;
    run((a) => a.logApplied(shown));
    await settle();
    expect(text('row3')).toBe('done');
    expect(text('done')).toBe('3');
    expect(text('shell-done')).toBe('3');
    expect(spy).not.toHaveBeenCalled();
  });

  it('a conflict from the client drops the overlay and toasts what happened', async () => {
    const { client, rowOf, act: run } = await open();
    vi.spyOn(client, 'logRequest').mockRejectedValue(new ApiError('conflict', 'Pylon changed since you loaded it. Nothing was logged. Reload and try again.'));
    run((a) => a.logRequest(rowOf(3)));
    await settle();
    expect(text('row3-steps')).toBe('x-');
    await settle(8000);
    await settle(50);
    expect(screen.getByText('Pylon changed since you loaded it. Nothing was logged. Reload and try again.')).toBeInTheDocument();
    expect(text('row3-steps')).toBe('--');
    expect(text('done')).toBe('2');
  });

  it('skip carries the row to tomorrow, leaves done alone and commits after the window', async () => {
    const { client, rowOf, act: run } = await open();
    const spy = vi.spyOn(client, 'skipRow');
    const row = rowOf(3);
    run((a) => a.skip(row));
    await settle();
    expect(screen.getByText('Carried to tomorrow: Pylon.')).toBeInTheDocument();
    expect(text('row3')).toBe('skipped');
    expect(text('done')).toBe('2');
    expect(text('shell-done')).toBe('2');
    await settle(8000);
    expect(spy).toHaveBeenCalledWith(row.entryId, row.revision, { requestId: expect.any(String) });
    await settle(50);
    expect(text('row3')).toBe('skipped');
    await act(() => Promise.resolve());
  });
});

describe('usePlanActions: notes and outcomes', () => {
  it('saveNote sends text, reason line and the note revision, and shows the saved text at once', async () => {
    const { client, rowOf, act: run } = await open();
    const spy = vi.spyOn(client, 'saveNote');
    const row = rowOf(3);
    run((a) => a.saveNote(row, 'Hi Lena, a shorter note.', 'She shipped usage billing (blog post).'));
    await settle(8000);
    expect(spy).toHaveBeenCalledWith(
      row.note?.noteId,
      { text: 'Hi Lena, a shorter note.', reasonLine: 'She shipped usage billing (blog post).', revision: row.note?.revision },
      { requestId: expect.any(String) },
    );
    await settle(50);
    expect((await client.plan()).rows[2]?.note?.text).toBe('Hi Lena, a shorter note.');
  });

  it('two saves inside the window both go out, in order, and the server ends with the second text', async () => {
    const { client, rowOf, act: run } = await open();
    const spy = vi.spyOn(client, 'saveNote');
    const row = rowOf(3);
    run((a) => a.saveNote(row, 'Hi Lena, first version.', row.note?.reasonLine ?? ''));
    await settle(2000);
    run((a) => a.saveNote(row, 'Hi Lena, second version.', row.note?.reasonLine ?? ''));
    await settle();
    expect(screen.getAllByText('Saved the note for Lena Ortiz.')).toHaveLength(2);
    await settle(10000);
    await settle(50);
    expect(spy).toHaveBeenCalledTimes(2);
    const first = (await spy.mock.results[0]?.value) as { revision: number };
    expect(spy.mock.calls[0]?.[1].text).toBe('Hi Lena, first version.');
    expect(spy.mock.calls[1]?.[1].text).toBe('Hi Lena, second version.');
    expect(spy.mock.calls[1]?.[1].revision).toBe(first.revision);
    expect((await client.plan()).rows[2]?.note?.text).toBe('Hi Lena, second version.');
  });

  it('a save while the first one is settling is not dropped', async () => {
    const { client, rowOf, act: run } = await open();
    const spy = vi.spyOn(client, 'saveNote');
    const row = rowOf(3);
    run((a) => a.saveNote(row, 'Hi Lena, one.', row.note?.reasonLine ?? ''));
    await settle(8000);
    run((a) => a.saveNote(row, 'Hi Lena, two.', row.note?.reasonLine ?? ''));
    await settle(8000);
    await settle(50);
    expect(spy).toHaveBeenCalledTimes(2);
    expect((await client.plan()).rows[2]?.note?.text).toBe('Hi Lena, two.');
  });

  it('confirmReason confirms the line with the note revision', async () => {
    const { client, rowOf, act: run } = await open();
    const spy = vi.spyOn(client, 'confirmReason');
    const row = rowOf(3);
    run((a) => a.confirmReason(row));
    await settle(8000);
    expect(spy).toHaveBeenCalledWith(row.note?.noteId, row.note?.revision, { requestId: expect.any(String) });
    await settle(50);
    expect((await client.plan()).rows[2]?.note?.needsYourLine).toBe(false);
  });

  const touchOf = (prompt: { touches: PromptTouch[] }) => prompt.touches[0] as PromptTouch;

  it('answer accepted takes the touch off the prompt at once, commits after the window and undo drops it', async () => {
    const { client, prompt, act: run } = await open();
    const spy = vi.spyOn(client, 'logOutcome');
    const t = touchOf(prompt);
    expect(text('prompt')).toBe('2');
    run((a) => a.answer(t, 'accepted'));
    await settle();
    expect(text('prompt')).toBe('1');
    expect(screen.getByText(`Logged: ${t.person.name} accepted.`)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    await settle(10000);
    expect(spy).not.toHaveBeenCalled();
    expect(text('prompt')).toBe('2');

    run((a) => a.answer(t, 'accepted'));
    await settle(8000);
    expect(spy).toHaveBeenCalledWith(t.touchId, 'accepted', t.revision, { requestId: expect.any(String) });
    await settle(50);
    expect(text('prompt')).toBe('1');
  });

  it('answer said_no commits at once, with the reply kind', async () => {
    const { client, prompt, act: run } = await open();
    const spy = vi.spyOn(client, 'logOutcome');
    const t = touchOf(prompt);
    run((a) => a.answer(t, 'replied', 'said_no'));
    await settle();
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith(t.touchId, 'replied', t.revision, { requestId: expect.any(String) }, 'said_no');
    expect(screen.queryByRole('button', { name: 'Undo' })).not.toBeInTheDocument();
  });

  it('answer asked_to_stop commits at once, and an interested reply waits for the window', async () => {
    const { client, prompt, act: run } = await open();
    const spy = vi.spyOn(client, 'logOutcome');
    const [first, second] = prompt.touches as [PromptTouch, PromptTouch];
    run((a) => a.answer(first, 'replied', 'asked_to_stop'));
    await settle();
    expect(spy).toHaveBeenCalledTimes(1);
    run((a) => a.answer(second, 'replied', 'interested'));
    await settle(7000);
    expect(spy).toHaveBeenCalledTimes(1);
    await settle(1000);
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it('answer quiet sends the quiet outcome and takes the touch off the prompt', async () => {
    const { client, prompt, act: run } = await open();
    const spy = vi.spyOn(client, 'logOutcome');
    const t = touchOf(prompt);
    run((a) => a.answer(t, 'quiet'));
    await settle(8000);
    expect(spy).toHaveBeenCalledWith(t.touchId, 'quiet', t.revision, { requestId: expect.any(String) });
    await settle(50);
    expect(text('prompt')).toBe('1');
  });

  it('seeWins marks the given wins seen at once with no toast, and leaves the others unseen', async () => {
    const { client, act: run } = await open('win');
    const wins = await client.wins();
    const unseen = wins.wins.filter((w) => w.unseen);
    expect(unseen.length).toBeGreaterThan(0);
    const spy = vi.spyOn(client, 'markWinsSeen');
    const ids: Id[] = unseen.map((w) => w.winId);
    const before = Number(text('unseen'));
    run((a) => a.seeWins(ids));
    await settle();
    expect(text('unseen')).toBe(String(before - ids.length));
    expect(spy).toHaveBeenCalledWith(ids, { requestId: expect.any(String) });
    expect(screen.queryByRole('button', { name: 'Undo' })).not.toBeInTheDocument();
    expect(screen.queryByText('Marked as seen.')).not.toBeInTheDocument();
    await settle(50);
    expect(text('unseen')).toBe('0');
  });
});
