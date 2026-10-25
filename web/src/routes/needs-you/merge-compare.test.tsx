import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReviewItem } from '@/api';
import { buildFixture } from '@/api/fixture/data';
import { renderWithApp } from '@/test/render';
import { MergeCompare } from './merge-compare';

function mergeItem(): ReviewItem {
  const found = buildFixture('review').needsYou.open.find((r) => r.kind === 'merge_candidate');
  if (!found?.merge) throw new Error('No merge question in the review scenario');
  return found;
}

function show(item: ReviewItem) {
  const onMerge = vi.fn();
  const onKeepSeparate = vi.fn();
  const view = renderWithApp(
    <div className="grid grid-cols-[20px_minmax(0,1fr)_auto]">
      <MergeCompare item={item} onMerge={onMerge} onKeepSeparate={onKeepSeparate} />
    </div>,
  );
  return Object.assign(view, { onMerge, onKeepSeparate });
}

const rowNamed = (name: string) => screen.getByRole('rowheader', { name }).closest('tr') as HTMLElement;

describe('MergeCompare', () => {
  it('the merge row shows both companies side by side and marks only the differing facts', () => {
    const { container } = show(mergeItem());
    const table = screen.getByRole('table', { name: 'Mesh Systems and Mesh, side by side' });
    const heads = within(table).getAllByRole('columnheader').map((h) => h.textContent);
    expect(heads).toEqual(['Fact', 'Mesh SystemsSuggestedEmailing', 'MeshBacklog', 'Same or different']);

    const differing = [...container.querySelectorAll('tr[data-differs]')].map((tr) => tr.querySelector('th')?.textContent);
    expect(differing).toEqual(['Domain', 'Boards', 'Open postings', 'People', 'Touches']);
    expect(rowNamed('Domain')).toHaveTextContent('meshsystems.examplemesh.exampleDiffers');
    expect(rowNamed('Boards')).toHaveTextContent('Ashby, meshsystemsGreenhouse, meshDiffers');
    expect(rowNamed('Touches')).toHaveTextContent('60Differs');
    // A matching row has no mark: color is never the only signal, so the word is there when they differ.
    expect(rowNamed('Size')).not.toHaveTextContent('Differs');
    expect(rowNamed('Stage')).toHaveTextContent('Series ASeries A');
    expect(rowNamed('Stage')).not.toHaveAttribute('data-differs');

    expect(
      screen.getByText(
        "Mesh Systems is suggested because your 6 touches are there. Merging keeps Mesh Systems, folds Mesh into it, and sends anything found later under Mesh's name to Mesh Systems. You can undo it from Resolved.",
      ),
    ).toBeInTheDocument();
  });

  it('offers Merge into the suggested one on M, the other with no key, and Keep separate on O', async () => {
    const user = userEvent.setup();
    const { onMerge, onKeepSeparate } = show(mergeItem());
    const suggested = screen.getByRole('button', { name: 'Merge into Mesh Systems' });
    expect(suggested).toHaveAttribute('aria-keyshortcuts', 'm');
    const other = screen.getByRole('button', { name: 'Merge into Mesh' });
    expect(other).not.toHaveAttribute('aria-keyshortcuts');
    const keep = screen.getByRole('button', { name: 'Keep separate' });
    expect(keep).toHaveAttribute('aria-keyshortcuts', 'o');

    await user.click(suggested);
    expect(onMerge).toHaveBeenLastCalledWith('a');
    await user.click(other);
    expect(onMerge).toHaveBeenLastCalledWith('b');
    await user.click(keep);
    expect(onKeepSeparate).toHaveBeenCalledTimes(1);
  });

  it('suggests the second company when the API does, and says a tie warning', () => {
    const item = mergeItem();
    if (!item.merge) throw new Error('no merge');
    item.merge = {
      ...item.merge,
      a: { ...item.merge.a, touches: 0, people: 0 },
      b: { ...item.merge.b, touches: 0, people: 3 },
      suggestedKeep: 'b',
      warning: 'This record collects name-only mentions that could be either company.',
    };
    show(item);
    expect(screen.getByRole('button', { name: 'Merge into Mesh' })).toHaveAttribute('aria-keyshortcuts', 'm');
    expect(screen.getByRole('button', { name: 'Merge into Mesh Systems' })).not.toHaveAttribute('aria-keyshortcuts');
    expect(screen.getByText(/^Mesh is suggested because 3 people are on file there\./)).toBeInTheDocument();
    expect(screen.getByText('This record collects name-only mentions that could be either company.')).toBeInTheDocument();
  });
});
