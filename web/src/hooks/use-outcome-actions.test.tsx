import { useEffect } from 'react';
import { act, fireEvent, screen } from '@testing-library/react';
import { queryKeys, useClient, type Id, type PromptTouch } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { buildFixture, type Scenario } from '@/api/fixture/data';
import { useViewQuery } from '@/pending/pending';
import { renderWithApp } from '@/test/render';
import { useOutcomeActions } from './use-outcome-actions';

type Api = ReturnType<typeof useOutcomeActions>;
const api: { current?: Api } = {};

function Harness() {
  const client = useClient();
  const actions = useOutcomeActions();
  const prompt = useViewQuery(queryKeys.outcomePrompt, () => client.outcomePrompt());
  const wins = useViewQuery(queryKeys.wins, () => client.wins());
  useEffect(() => {
    api.current = actions;
  });
  return (
    <>
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
  const prompt = await client.outcomePrompt();
  renderWithApp(<Harness />, { route: '/pipeline', client });
  await settle();
  const act_ = <T,>(fn: (a: Api) => T): T => {
    let out: T | undefined;
    act(() => {
      out = fn(api.current as Api);
    });
    return out as T;
  };
  return { client, prompt, act: act_ };
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

describe('useOutcomeActions', () => {
  const touchOf = (prompt: { touches: PromptTouch[] }) => prompt.touches[0] as PromptTouch;

  it('answer accepted takes the touch off the prompt at once, commits after the window and undo drops it', async () => {
    const { client, prompt, act: run } = await open();
    const spy = vi.spyOn(client, 'logOutcome');
    const t = touchOf(prompt);
    expect(text('prompt')).toBe('2');
    run((a) => a.answer(t, 'accepted'));
    await settle();
    expect(text('prompt')).toBe('1');
    expect(screen.getByText(`Logged: ${t.person?.name} accepted.`)).toBeInTheDocument();
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
    // An immediate write goes out with keepalive, so it finishes even if the tab closes now (pending store, #11).
    expect(spy).toHaveBeenCalledWith(t.touchId, 'replied', t.revision, { requestId: expect.any(String), keepalive: true }, 'said_no');
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
    expect(spy).toHaveBeenCalledWith(ids, { requestId: expect.any(String), keepalive: true });
    expect(screen.queryByRole('button', { name: 'Undo' })).not.toBeInTheDocument();
    expect(screen.queryByText('Marked as seen.')).not.toBeInTheDocument();
    await settle(50);
    expect(text('unseen')).toBe('0');
  });

  it('seeWins with undo shows the toast with Undo, commits after the window, and undo takes it back', async () => {
    const { client, act: run } = await open('win');
    const ids: Id[] = (await client.wins()).wins.filter((w) => w.unseen).map((w) => w.winId);
    const spy = vi.spyOn(client, 'markWinsSeen');
    run((a) => a.seeWins(ids, { undo: true }));
    await settle();
    expect(text('unseen')).toBe('0');
    expect(screen.getByText('Marked seen.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();
    await settle(7999);
    expect(spy).not.toHaveBeenCalled();
    await settle(1);
    expect(spy).toHaveBeenCalledWith(ids, { requestId: expect.any(String) });
  });

  it('seeWins with undo: undo inside the window sends nothing and the win is unseen again', async () => {
    const { client, act: run } = await open('win');
    const ids: Id[] = (await client.wins()).wins.filter((w) => w.unseen).map((w) => w.winId);
    const spy = vi.spyOn(client, 'markWinsSeen');
    run((a) => a.seeWins(ids, { undo: true }));
    await settle();
    expect(text('unseen')).toBe('0');
    act(() => {
      fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    });
    await settle();
    expect(text('unseen')).toBe(String(ids.length));
    await settle(8000);
    expect(spy).not.toHaveBeenCalled();
  });
});

