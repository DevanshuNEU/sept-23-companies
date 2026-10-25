import { useState } from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, useLocation, useNavigationType } from 'react-router';
import { HotkeysProvider } from '@/keyboard/hotkeys';
import { useListNav } from '@/keyboard/list-nav';

const people: Record<string, string> = {
  t1: 'Kenji Watanabe, Trigger.dev, blocked',
  t2: 'Omar Haddad, Mintlify, ready',
  t3: 'Dana Whitfield, Linear, ready',
};

function Probe() {
  const location = useLocation();
  const type = useNavigationType();
  return (
    <p data-testid="location">
      {location.pathname}
      {location.search} {type}
    </p>
  );
}

function List<T extends string | number>({
  ids,
  param,
  onOpen,
  announce,
  initial,
}: {
  ids: T[];
  param?: string;
  onOpen?: (id: T) => void;
  announce?: (id: T) => string;
  initial?: T;
}) {
  const nav = useListNav({ ids, param, onOpen, announce, initial });
  return (
    <>
      <p data-testid="selected">{nav.selected === null ? 'none' : String(nav.selected)}</p>
      <ul>
        {ids.map((id) => (
          <li key={id} {...nav.rowProps(id)}>
            row {id}
          </li>
        ))}
      </ul>
    </>
  );
}

function renderList<T extends string | number>(
  props: { ids: T[]; param?: string; onOpen?: (id: T) => void; announce?: (id: T) => string; initial?: T },
  route = '/outbox',
) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <HotkeysProvider>
        <List {...props} />
        <Probe />
      </HotkeysProvider>
    </MemoryRouter>,
  );
}

const press = (key: string, init: KeyboardEventInit = {}) => fireEvent.keyDown(document.body, { key, ...init });
const location = () => screen.getByTestId('location').textContent;
const selected = () => screen.getByTestId('selected').textContent;

describe('useListNav', () => {
  it('j and k move without wrapping', () => {
    renderList({ ids: ['t1', 't2', 't3'] });
    expect(selected()).toBe('none');

    press('j');
    expect(selected()).toBe('t1');
    expect(location()).toBe('/outbox?sel=t1 REPLACE');
    press('j');
    press('j');
    expect(selected()).toBe('t3');
    press('j');
    expect(selected()).toBe('t3');

    press('k');
    expect(selected()).toBe('t2');
    press('k');
    press('k');
    expect(selected()).toBe('t1');
    expect(location()).toBe('/outbox?sel=t1 REPLACE');
  });

  it('j and k step from the row he moved to last, even before the URL has rendered', () => {
    renderList({ ids: ['t1', 't2', 't3'] });
    act(() => {
      press('j');
      press('j');
      press('j');
      press('k');
    });
    expect(selected()).toBe('t2');
  });

  it('arrow keys move too', () => {
    renderList({ ids: ['t1', 't2', 't3'] });
    press('ArrowDown');
    press('ArrowDown');
    expect(selected()).toBe('t2');
    press('ArrowUp');
    expect(selected()).toBe('t1');
  });

  it('reads the selection from ?sel= and marks the row', () => {
    renderList({ ids: ['t1', 't2', 't3'] }, '/outbox?sel=t2&tab=drafts');
    expect(selected()).toBe('t2');
    const row = screen.getByText('row t2');
    expect(row).toHaveAttribute('aria-selected', 'true');
    expect(row).toHaveAttribute('data-active', 'true');
    expect(screen.getByText('row t1')).toHaveAttribute('aria-selected', 'false');

    press('j');
    expect(location()).toBe('/outbox?sel=t3&tab=drafts REPLACE');
  });

  it('an id that is not in the list selects nothing', () => {
    renderList({ ids: ['t1', 't2'] }, '/outbox?sel=gone');
    expect(selected()).toBe('none');
  });

  it('with initial the selection is the initial row and the URL is untouched', () => {
    renderList({ ids: ['t1', 't2', 't3'], initial: 't2' });
    expect(selected()).toBe('t2');
    expect(location()).toBe('/outbox ' + 'POP');
    expect(screen.getByText('row t2')).toHaveAttribute('aria-selected', 'true');
  });

  it('j moves from the initial row and writes the URL then', () => {
    renderList({ ids: ['t1', 't2', 't3'], initial: 't2' });
    press('j');
    expect(selected()).toBe('t3');
    expect(location()).toBe('/outbox?sel=t3 REPLACE');
  });

  it('the URL selection wins over initial, and a sel that is not in the list selects nothing', () => {
    const { unmount } = renderList({ ids: ['t1', 't2', 't3'], initial: 't2' }, '/outbox?sel=t1');
    expect(selected()).toBe('t1');
    unmount();
    renderList({ ids: ['t1', 't2', 't3'], initial: 't2' }, '/outbox?sel=gone');
    expect(selected()).toBe('none');
  });

  it('an initial that is not in ids selects nothing', () => {
    renderList({ ids: ['t1', 't2'], initial: 'gone' });
    expect(selected()).toBe('none');
    press('j');
    expect(selected()).toBe('t1');
  });

  it('works with number ids and another param', () => {
    renderList({ ids: [3, 5, 8], param: 'company' }, '/no-go?company=5');
    expect(selected()).toBe('5');
    press('j');
    expect(location()).toBe('/no-go?company=8 REPLACE');
  });

  it('Enter calls onOpen with the selected id', () => {
    const onOpen = vi.fn();
    renderList({ ids: ['t1', 't2'], onOpen });
    press('Enter');
    expect(onOpen).not.toHaveBeenCalled();
    press('j');
    press('j');
    press('Enter');
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onOpen).toHaveBeenCalledWith('t2');
  });

  it('a click selects the row and scrolls a key move into view', () => {
    renderList({ ids: ['t1', 't2', 't3'] });
    fireEvent.click(screen.getByText('row t3'));
    expect(selected()).toBe('t3');
    expect(location()).toBe('/outbox?sel=t3 REPLACE');

    const scroll = vi.mocked(Element.prototype.scrollIntoView);
    scroll.mockClear();
    press('k');
    expect(scroll).toHaveBeenCalledTimes(1);
    expect(scroll.mock.contexts[0]).toBe(screen.getByText('row t2'));
  });

  it('announces the selection', () => {
    renderList({ ids: ['t1', 't2', 't3'], announce: (id) => people[id] ?? id });
    const region = screen.getByRole('status');
    expect(region).toHaveAttribute('aria-live', 'polite');
    expect(region).toHaveTextContent('');

    press('j');
    expect(region).toHaveTextContent('Kenji Watanabe, Trigger.dev, blocked');
    press('j');
    expect(region).toHaveTextContent('Omar Haddad, Mintlify, ready');
  });
});

describe('a selected row that leaves the list', () => {
  /** A list where Settle takes the selected row out at once and moves to the next one, as a Needs you fix does. */
  function Settling({ seen }: { seen: string[] }) {
    const [ids, setIds] = useState(['a', 'b', 'c']);
    const nav = useListNav({ ids });
    seen.push(nav.selected ?? 'none');
    return (
      <>
        <ul>
          {ids.map((id) => (
            <li key={id} {...nav.rowProps(id)}>
              row {id}
            </li>
          ))}
        </ul>
        <button
          type="button"
          onClick={() => {
            const at = ids.indexOf(nav.selected ?? '');
            const next = ids[at + 1] ?? ids[at - 1];
            if (next !== undefined) nav.select(next);
            setIds((list) => list.filter((x) => x !== nav.selected));
          }}
        >
          Settle
        </button>
      </>
    );
  }

  it('hands the selection to the row he moved to, with no render that selects nothing', () => {
    const seen: string[] = [];
    render(
      <MemoryRouter initialEntries={['/needs-you']}>
        <HotkeysProvider>
          <Settling seen={seen} />
        </HotkeysProvider>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByText('row a'));
    seen.length = 0;
    fireEvent.click(screen.getByRole('button', { name: 'Settle' }));
    expect(screen.queryByText('row a')).not.toBeInTheDocument();
    expect(screen.getByText('row b')).toHaveAttribute('aria-selected', 'true');
    // Every render after the fix shows b: a render that selected nothing would end the move to b, and a key
    // pressed in that moment would be lost.
    expect(seen.length).toBeGreaterThan(0);
    expect(seen.filter((s) => s !== 'b')).toEqual([]);
  });
});
