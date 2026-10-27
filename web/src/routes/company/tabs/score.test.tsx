import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router';
import { ApiError, type Id, type ScoreView } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { companyId } from '@/api/fixture/data';
import { renderWithApp } from '@/test/render';
import CompanyPage from '../index';
import { wholePercents } from './score-shares';

const PYLON = companyId('Pylon');
const MINTLIFY = companyId('Mintlify');
const press = (key: string) => fireEvent.keyDown(document.body, { key });

function Where() {
  const { pathname, search } = useLocation();
  return <p data-testid="where">{pathname + search}</p>;
}

/** A client whose score read can be bent by the test. */
class ScoreClient extends FixtureClient {
  constructor(private readonly bend: (s: ScoreView, id: Id) => ScoreView | Promise<ScoreView> = (s) => s) {
    super();
  }
  override async score(id: Id): Promise<ScoreView> {
    return this.bend(await super.score(id), id);
  }
}

async function openScore(id: Id = PYLON, client: FixtureClient = new ScoreClient()) {
  const view = renderWithApp(
    <>
      <CompanyPage />
      <Where />
    </>,
    { route: `/companies/${id}?tab=score`, client },
  );
  await screen.findByRole('region', { name: 'Facts' });
  await screen.findByRole('table', { name: 'Score by signal' });
  return view;
}

const rowOf = (label: string) => {
  const row = within(screen.getByRole('table', { name: 'Score by signal' })).getByRole('row', { name: new RegExp(`^${label}`) });
  return row;
};
const cells = (row: HTMLElement) => within(row).getAllByRole('cell');

describe('Score tab', () => {
  it('shows the score, the rank, the lanes and the one line the ranker gave', async () => {
    const view = await openScore();
    const s = await view.client.score(PYLON);
    expect(screen.getByRole('heading', { level: 2, name: `Score ${s.score.toFixed(2)}, rank ${s.rank} of ${s.ofCompanies.toLocaleString('en-US')}` })).toBeInTheDocument();
    expect(screen.getByText(/^Lanes:/)).toHaveTextContent('Lanes: Role at your level (strongest), Open US role');
    expect(screen.getByText(s.rankReason)).toBeInTheDocument();
  });

  it('lists every signal with its reason and share', async () => {
    await openScore();
    const table = screen.getByRole('table', { name: 'Score by signal' });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows.map((r) => within(r).getAllByRole('rowheader')[0]?.textContent)).toEqual([
      'Level match', 'Sponsorship', 'Posting age', 'Funding stage', 'Open engineering roles', 'Company size', 'People to ask', 'Email domain',
    ]);
    const level = cells(rowOf('Level match'));
    expect(level[1]).toHaveTextContent('+1.80');
    expect(level[1]).toHaveTextContent('0.9 × 2');
    expect(level[2]).toHaveTextContent('35%');
    expect(level[3]).toHaveTextContent('Software Engineer I is open, which matches your level.');
    expect(within(level[3] as HTMLElement).getByRole('link', { name: 'Pylon careers page' })).toHaveAttribute('href', expect.stringContaining('pylon'));
  });

  it("the profile judge's row says what the company builds, or that it is not judged yet", async () => {
    const view = await openScore(MINTLIFY);
    const s = await view.client.score(MINTLIFY);
    const fit = s.signals.find((x) => x.name === 'profile_fit');
    expect(fit?.label).toBe('Fits what you build');
    expect(fit?.value).toBeGreaterThanOrEqual(0);
    expect(rowOf('Fits what you build')).toHaveTextContent(fit?.value ? /Judge, fit \d+: Builds/ : /Not judged yet/);
  });

  it('the shares printed add to 100 percent of the absolute total', async () => {
    await openScore();
    const shares = within(screen.getByRole('table', { name: 'Score by signal' }))
      .getAllByRole('row')
      .slice(1)
      .map((r) => cells(r)[2]?.textContent ?? '');
    const total = shares.reduce((n, t) => n + Math.abs(Number(t.replace('%', ''))), 0);
    expect(total).toBe(100);
  });

  it('a negative signal draws left and reads as a cost', async () => {
    await openScore();
    const row = rowOf('People to ask');
    expect(cells(row)[2]).toHaveTextContent('-10%');
    expect(cells(row)[1]).toHaveTextContent('-0.50');
    const bar = within(row).getByRole('img');
    expect(bar).toHaveAccessibleName('Lowers the score by 10 percent of the total');
    expect(bar.querySelector('i')).toHaveAttribute('data-side', 'left');
    expect(within(rowOf('Level match')).getByRole('img').querySelector('i')).toHaveAttribute('data-side', 'right');
    expect(within(rowOf('Level match')).getByRole('img')).toHaveAccessibleName('Adds 35 percent of the score');
  });

  it('bars grow from the centre and scale to the largest share, never under 2 px', async () => {
    await openScore();
    const widest = within(rowOf('Level match')).getByRole('img').querySelector('i');
    expect(widest?.style.width).toBe('100%');
    const small = within(rowOf('Email domain')).getByRole('img').querySelector('i');
    expect(small?.style.minWidth).toBe('2px');
    expect(Number.parseFloat(small?.style.width ?? '')).toBeLessThan(15);
  });

  it('the neutral size signal says "No data, scored neutral" and has no bar width', async () => {
    await openScore();
    const row = rowOf('Company size');
    expect(within(row).getByText('No data, scored neutral')).toBeInTheDocument();
    expect(row.querySelector('i')).toBeNull();
    expect(within(row).getByRole('img')).toHaveAccessibleName('No data, scored neutral');
    expect(cells(row)[2]).toHaveTextContent('0%');
  });

  it('says how sure a signal is only when it is not high', async () => {
    await openScore();
    // Sourced signals are high and say nothing more; one with no source says what it rests on.
    expect(rowOf('Level match')).not.toHaveTextContent('confidence');
    expect(rowOf('Open engineering roles')).toHaveTextContent(/Medium confidence\. Worked out from what the crawler stored\./);
    expect(rowOf('Company size')).toHaveTextContent(/Low confidence\. Nothing is known yet, so it counts for nothing\./);
    expect(screen.getByText(/signals were read from a linked page/)).toBeInTheDocument();
  });

  it('a source with an unsafe URL is plain text', async () => {
    const client = new ScoreClient((s) => ({ ...s, signals: s.signals.map((sig) => (sig.source ? { ...sig, source: { ...sig.source, url: 'javascript:alert(1)' } } : sig)) }));
    await openScore(PYLON, client);
    expect(document.querySelector('a[href^="javascript:"]')).toBeNull();
    const level = cells(rowOf('Level match'))[3] as HTMLElement;
    expect(within(level).queryByRole('link')).toBeNull();
    expect(within(level).getByText('Pylon careers page')).toBeInTheDocument();
    expect(level).toHaveTextContent('No link');
  });

  it('the previous version line shows only when set', async () => {
    await openScore();
    expect(screen.getByText(/Ranker 2026\.11\.02\. Weights live in the ranking config\. A change makes a new version\./)).toBeInTheDocument();
    expect(screen.getByText(/Was 3\.70 under 2026\.10\.28\./)).toBeInTheDocument();
    expect(screen.getByText(/Scored today at 8:00\./)).toBeInTheDocument();
  });

  it('no previous version means no "Was" line', async () => {
    await openScore(MINTLIFY);
    expect(screen.getByText(/Ranker 2026\.11\.02\./)).toBeInTheDocument();
    expect(within(screen.getByRole('tabpanel')).queryByText(/Was \d/)).not.toBeInTheDocument();
  });

  it('a hidden company has no place and says so', async () => {
    const client = new ScoreClient((s) => ({ ...s, rank: null, rankReason: 'Hidden from the list, so it keeps its score and has no place.' }));
    const view = await openScore(PYLON, client);
    const s = await view.client.score(PYLON);
    expect(screen.getByRole('heading', { level: 2, name: `Score ${s.score.toFixed(2)}, no rank` })).toBeInTheDocument();
    expect(screen.getByText('Hidden from the list, so it keeps its score and has no place.')).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent('rank null');
  });

  it('key 7 opens the Score tab and puts it in the URL', async () => {
    renderWithApp(
      <>
        <CompanyPage />
        <Where />
      </>,
      { route: `/companies/${PYLON}`, client: new ScoreClient() },
    );
    await screen.findByRole('region', { name: 'Facts' });
    expect(screen.getByRole('tab', { name: /^Score/ })).toBeInTheDocument();
    act(() => {
      press('7');
    });
    expect(screen.getByRole('tab', { name: /^Score/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('where')).toHaveTextContent(`/companies/${PYLON}?tab=score`);
    expect(await screen.findByRole('table', { name: 'Score by signal' })).toBeInTheDocument();
  });

  it('the tab count is the number of signals', async () => {
    await openScore();
    await waitFor(() => expect(screen.getByRole('tab', { name: /^Score/ })).toHaveTextContent('8'));
  });

  it('a failing score read shows the partial state with Retry and the other tabs still work', async () => {
    class Flaky extends FixtureClient {
      fail = true;
      override score(id: Id): Promise<ScoreView> {
        return this.fail ? Promise.reject(new ApiError('network', 'Cannot reach the Knock server')) : super.score(id);
      }
    }
    const client = new Flaky();
    const user = userEvent.setup();
    renderWithApp(<CompanyPage />, { route: `/companies/${PYLON}?tab=score`, client });
    await screen.findByRole('region', { name: 'Facts' });
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Score did not load for this company.');
    expect(screen.getByRole('heading', { level: 1, name: 'Pylon' })).toBeInTheDocument();
    // Another tab still works.
    act(() => {
      press('2');
    });
    expect(screen.getByRole('tab', { name: /^Roles/ })).toHaveAttribute('aria-selected', 'true');
    act(() => {
      press('7');
    });
    client.fail = false;
    await user.click(await screen.findByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('table', { name: 'Score by signal' })).toBeInTheDocument();
  });

  it('a company with no score yet says so', async () => {
    const client = new ScoreClient(() => {
      throw new ApiError('not_found', 'No score');
    });
    renderWithApp(<CompanyPage />, { route: `/companies/${PYLON}?tab=score`, client });
    await screen.findByRole('region', { name: 'Facts' });
    expect(await screen.findByText('No score yet for this company.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
  });
});

describe('wholePercents', () => {
  it('keeps the signs and makes the absolute values add to 100', () => {
    const out = wholePercents([0.3529, 0.2353, 0.1765, 0.0588, 0.0392, 0, -0.098, 0.0392]);
    expect(out).toEqual([35, 23, 18, 6, 4, 0, -10, 4]);
    expect(out.reduce((n, x) => n + Math.abs(x), 0)).toBe(100);
  });
  it('is all zero when every signal is neutral', () => {
    expect(wholePercents([0, 0, 0])).toEqual([0, 0, 0]);
  });
  it('splits an even share exactly', () => {
    expect(wholePercents([0.5, 0.5])).toEqual([50, 50]);
  });
});
