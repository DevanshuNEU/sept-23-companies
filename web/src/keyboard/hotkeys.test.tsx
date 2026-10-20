import { act, fireEvent, render, screen } from '@testing-library/react';
import { useState, type ReactNode } from 'react';
import {
  HotkeysProvider,
  isTypingTarget,
  useHotkeyRegistry,
  useHotkeys,
  type Binding,
} from '@/keyboard/hotkeys';

function Keys({ bindings, children }: { bindings: Binding[]; children?: ReactNode }) {
  useHotkeys(bindings);
  return <>{children}</>;
}

function bind(keys: string, extra: Partial<Binding> = {}): Binding & { run: ReturnType<typeof vi.fn> } {
  return { keys, label: `Label ${keys}`, group: 'Test', run: vi.fn(), ...extra } as Binding & {
    run: ReturnType<typeof vi.fn>;
  };
}

function press(target: Element | Document, key: string, init: KeyboardEventInit = {}): boolean {
  return fireEvent.keyDown(target, { key, ...init });
}

function setPlatform(platform: string) {
  vi.spyOn(navigator, 'platform', 'get').mockReturnValue(platform);
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('useHotkeys', () => {
  it('single keys are ignored while typing', () => {
    const a = bind('a');
    render(
      <HotkeysProvider>
        <Keys bindings={[a]}>
          <input aria-label="name" />
          <textarea aria-label="body" />
          <div contentEditable="true" suppressContentEditableWarning data-testid="rich">
            <span data-testid="rich-child">text</span>
          </div>
        </Keys>
      </HotkeysProvider>,
    );
    press(screen.getByLabelText('name'), 'a');
    press(screen.getByLabelText('body'), 'a');
    press(screen.getByTestId('rich'), 'a');
    press(screen.getByTestId('rich-child'), 'a');
    expect(a.run).not.toHaveBeenCalled();

    press(document.body, 'a');
    expect(a.run).toHaveBeenCalledTimes(1);
  });

  it('single keys are suspended while a dialog is open', () => {
    const a = bind('a');
    const escape = bind('escape');
    const { rerender } = render(
      <HotkeysProvider>
        <Keys bindings={[a, escape]}>
          <div role="dialog">Close company</div>
        </Keys>
      </HotkeysProvider>,
    );
    press(document.body, 'a');
    expect(a.run).not.toHaveBeenCalled();
    press(document.body, 'Escape');
    expect(escape.run).toHaveBeenCalledTimes(1);

    rerender(
      <HotkeysProvider>
        <Keys bindings={[a, escape]}>
          <div role="alertdialog">Resume sending</div>
        </Keys>
      </HotkeysProvider>,
    );
    press(document.body, 'a');
    expect(a.run).not.toHaveBeenCalled();
    press(document.body, 'Escape');
    expect(escape.run).toHaveBeenCalledTimes(2);

    rerender(
      <HotkeysProvider>
        <Keys bindings={[a, escape]} />
      </HotkeysProvider>,
    );
    press(document.body, 'a');
    expect(a.run).toHaveBeenCalledTimes(1);
  });

  it.each(['dialog', 'alertdialog'])('mod keys are suspended under role=%s', (role) => {
    setPlatform('MacIntel');
    const saveApprove = bind('mod+enter', { label: 'Save, then approve' });
    const save = bind('mod+s');
    function CloseDialog() {
      return (
        <div role={role}>
          <textarea aria-label="Reason" />
        </div>
      );
    }
    render(
      <HotkeysProvider>
        <Keys bindings={[saveApprove, save]}>
          <CloseDialog />
        </Keys>
      </HotkeysProvider>,
    );
    const reason = screen.getByLabelText('Reason');
    expect(press(reason, 'Enter', { metaKey: true })).toBe(true);
    press(reason, 's', { metaKey: true });
    press(document.body, 'Enter', { metaKey: true });
    expect(saveApprove.run).not.toHaveBeenCalled();
    expect(save.run).not.toHaveBeenCalled();
  });

  it('a binding with inDialogs runs while a dialog is open', () => {
    setPlatform('MacIntel');
    const confirm = bind('mod+enter', { label: 'Close company', inDialogs: true });
    const pick = bind('x', { inDialogs: true });
    render(
      <HotkeysProvider>
        <Keys bindings={[confirm, pick]}>
          <div role="dialog">
            <textarea aria-label="Reason" />
          </div>
        </Keys>
      </HotkeysProvider>,
    );
    press(screen.getByLabelText('Reason'), 'Enter', { metaKey: true });
    expect(confirm.run).toHaveBeenCalledTimes(1);
    press(document.body, 'x');
    expect(pick.run).toHaveBeenCalledTimes(1);
  });

  it('caps lock still matches', () => {
    const a = bind('a');
    const j = bind('j');
    const shiftJ = bind('shift+j');
    render(
      <HotkeysProvider>
        <Keys bindings={[a, j, shiftJ]} />
      </HotkeysProvider>,
    );
    press(document.body, 'A');
    expect(a.run).toHaveBeenCalledTimes(1);

    press(document.body, 'J', { shiftKey: true });
    expect(shiftJ.run).toHaveBeenCalledTimes(1);
    expect(j.run).not.toHaveBeenCalled();

    press(document.body, 'j');
    expect(j.run).toHaveBeenCalledTimes(1);
    expect(shiftJ.run).toHaveBeenCalledTimes(1);
  });

  it('? matches with or without Shift', () => {
    const help = bind('?');
    render(
      <HotkeysProvider>
        <Keys bindings={[help]} />
      </HotkeysProvider>,
    );
    press(document.body, '?', { shiftKey: true });
    press(document.body, '?');
    expect(help.run).toHaveBeenCalledTimes(2);
  });

  it('mod keys work while typing', () => {
    const save = bind('mod+s');
    const escape = bind('escape');
    const a = bind('a');
    const pick = bind('x', { inInputs: true });
    render(
      <HotkeysProvider>
        <Keys bindings={[save, escape, a, pick]}>
          <textarea aria-label="body" />
        </Keys>
      </HotkeysProvider>,
    );
    const body = screen.getByLabelText('body');
    const notPrevented = press(body, 's', { ctrlKey: true });
    expect(save.run).toHaveBeenCalledTimes(1);
    expect(notPrevented).toBe(false);
    press(body, 'Escape');
    expect(escape.run).toHaveBeenCalledTimes(1);

    // Single keys stay off in a text field unless the binding sets inInputs.
    expect(press(body, 'a')).toBe(true);
    expect(a.run).not.toHaveBeenCalled();
    press(body, 'x');
    expect(pick.run).toHaveBeenCalledTimes(1);
  });

  it('mod is Cmd on a Mac and Ctrl elsewhere', () => {
    const save = bind('mod+s');
    render(
      <HotkeysProvider>
        <Keys bindings={[save]} />
      </HotkeysProvider>,
    );
    setPlatform('MacIntel');
    press(document.body, 's', { ctrlKey: true });
    expect(save.run).not.toHaveBeenCalled();
    press(document.body, 's', { metaKey: true });
    expect(save.run).toHaveBeenCalledTimes(1);

    setPlatform('Win32');
    press(document.body, 's', { metaKey: true });
    expect(save.run).toHaveBeenCalledTimes(1);
    press(document.body, 's', { ctrlKey: true });
    expect(save.run).toHaveBeenCalledTimes(2);

    // A plain key does not fire a mod binding, and a mod combo does not fire a plain binding.
    press(document.body, 's');
    expect(save.run).toHaveBeenCalledTimes(2);
  });

  it('mod+enter matches Enter with the platform modifier', () => {
    setPlatform('MacIntel');
    const enter = bind('enter');
    const saveApprove = bind('mod+enter');
    render(
      <HotkeysProvider>
        <Keys bindings={[enter, saveApprove]}>
          <textarea aria-label="body" />
        </Keys>
      </HotkeysProvider>,
    );
    press(screen.getByLabelText('body'), 'Enter', { metaKey: true });
    expect(saveApprove.run).toHaveBeenCalledTimes(1);
    expect(enter.run).not.toHaveBeenCalled();
  });

  it('g chord waits one second', () => {
    vi.useFakeTimers();
    const today = bind('g t');
    const outbox = bind('g o');
    const single = bind('g');
    const t = bind('t');
    render(
      <HotkeysProvider>
        <Keys bindings={[single, today, outbox, t]} />
      </HotkeysProvider>,
    );
    press(document.body, 'g');
    act(() => vi.advanceTimersByTime(900));
    press(document.body, 't');
    expect(today.run).toHaveBeenCalledTimes(1);
    expect(t.run).not.toHaveBeenCalled();
    expect(single.run).not.toHaveBeenCalled();

    press(document.body, 'g');
    act(() => vi.advanceTimersByTime(1100));
    press(document.body, 't');
    expect(today.run).toHaveBeenCalledTimes(1);
    expect(t.run).toHaveBeenCalledTimes(1);

    // Any other key cancels the chord and does nothing itself.
    press(document.body, 'g');
    press(document.body, 'x');
    press(document.body, 't');
    expect(today.run).toHaveBeenCalledTimes(1);
    expect(t.run).toHaveBeenCalledTimes(2);

    // Shift on its own does not cancel a pending chord.
    press(document.body, 'g');
    press(document.body, 'Shift', { shiftKey: true });
    press(document.body, 'o');
    expect(outbox.run).toHaveBeenCalledTimes(1);
    expect(single.run).not.toHaveBeenCalled();
  });

  it('a chord does not start while typing', () => {
    const today = bind('g t');
    render(
      <HotkeysProvider>
        <Keys bindings={[today]}>
          <input aria-label="search" />
        </Keys>
      </HotkeysProvider>,
    );
    const search = screen.getByLabelText('search');
    press(search, 'g');
    press(search, 't');
    press(document.body, 't');
    expect(today.run).not.toHaveBeenCalled();
  });

  it('ignores key repeat', () => {
    const a = bind('a');
    const today = bind('g t');
    render(
      <HotkeysProvider>
        <Keys bindings={[a, today]} />
      </HotkeysProvider>,
    );
    press(document.body, 'a', { repeat: true });
    press(document.body, 'g', { repeat: true });
    press(document.body, 't');
    expect(a.run).not.toHaveBeenCalled();
    expect(today.run).not.toHaveBeenCalled();

    press(document.body, 'a');
    press(document.body, 'a', { repeat: true });
    press(document.body, 'a', { repeat: true });
    expect(a.run).toHaveBeenCalledTimes(1);
  });

  it('skips a key another handler already took', () => {
    const escape = bind('escape');
    render(
      <HotkeysProvider>
        <Keys bindings={[escape]}>
          <div data-testid="layer" onKeyDown={(e) => e.preventDefault()} />
        </Keys>
      </HotkeysProvider>,
    );
    press(screen.getByTestId('layer'), 'Escape');
    expect(escape.run).not.toHaveBeenCalled();
  });

  it('Enter on a focused button presses the button, not the binding', () => {
    const open = bind('enter');
    render(
      <HotkeysProvider>
        <Keys bindings={[open]}>
          <button type="button">Approve</button>
          <a href="#x">Open in Gmail</a>
        </Keys>
      </HotkeysProvider>,
    );
    expect(press(screen.getByRole('button', { name: 'Approve' }), 'Enter')).toBe(true);
    expect(press(screen.getByRole('link', { name: 'Open in Gmail' }), 'Enter')).toBe(true);
    expect(open.run).not.toHaveBeenCalled();
    press(document.body, 'Enter');
    expect(open.run).toHaveBeenCalledTimes(1);
  });

  it('the last mounted binding wins a clash', () => {
    const shellEscape = bind('escape', { label: 'Go back' });
    const editorEscape = bind('escape', { label: 'Leave the editor' });
    const pageA = bind('a', { label: 'Page a' });
    const dialogA = bind('a', { label: 'Later a' });

    function Later() {
      const [shown, setShown] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setShown(true)}>
            Show
          </button>
          {shown && <Keys bindings={[dialogA]} />}
        </>
      );
    }

    render(
      <HotkeysProvider>
        <Keys bindings={[shellEscape]}>
          <Keys bindings={[editorEscape, pageA]}>
            <Later />
          </Keys>
        </Keys>
      </HotkeysProvider>,
    );
    press(document.body, 'Escape');
    expect(editorEscape.run).toHaveBeenCalledTimes(1);
    expect(shellEscape.run).not.toHaveBeenCalled();

    press(document.body, 'a');
    expect(pageA.run).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Show' }));
    press(document.body, 'a');
    expect(dialogA.run).toHaveBeenCalledTimes(1);
    expect(pageA.run).toHaveBeenCalledTimes(1);
  });

  it('a disabled binding never runs and does not hide an earlier one', () => {
    const shellEscape = bind('escape', { label: 'Go back' });
    const editorEscape = bind('escape', { label: 'Leave the editor', enabled: false });
    const approve = bind('a', { label: 'Approve', enabled: false });
    render(
      <HotkeysProvider>
        <Keys bindings={[shellEscape]}>
          <Keys bindings={[editorEscape, approve]} />
        </Keys>
      </HotkeysProvider>,
    );
    press(document.body, 'a');
    expect(approve.run).not.toHaveBeenCalled();
    press(document.body, 'Escape');
    expect(editorEscape.run).not.toHaveBeenCalled();
    expect(shellEscape.run).toHaveBeenCalledTimes(1);
  });

  it('calls the latest run after a rerender', () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = render(
      <HotkeysProvider>
        <Keys bindings={[{ keys: 'x', label: 'Pick', group: 'Today', run: first }]} />
      </HotkeysProvider>,
    );
    rerender(
      <HotkeysProvider>
        <Keys bindings={[{ keys: 'x', label: 'Pick', group: 'Today', run: second }]} />
      </HotkeysProvider>,
    );
    press(document.body, 'x');
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });

  it('stops listening when unmounted', () => {
    const a = bind('a');
    const { unmount } = render(
      <HotkeysProvider>
        <Keys bindings={[a]} />
      </HotkeysProvider>,
    );
    unmount();
    press(document.body, 'a');
    expect(a.run).not.toHaveBeenCalled();
  });

  it('throws outside HotkeysProvider', () => {
    const quiet = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => render(<Keys bindings={[bind('a')]} />)).toThrow('useHotkeys outside HotkeysProvider');
    quiet.mockRestore();
  });
});

describe('useHotkeyRegistry', () => {
  function Sheet() {
    const registry = useHotkeyRegistry();
    return (
      <ul>
        {registry.map((b) => (
          <li key={`${b.group} ${b.keys}`}>
            {b.group}: {b.keys} {b.label}
            {b.enabled === false ? ' (off)' : ''}
          </li>
        ))}
      </ul>
    );
  }

  it('lists the bindings that are mounted, disabled ones included, one per key', () => {
    function Page({ open }: { open: boolean }) {
      return (
        <Keys bindings={[{ keys: 'g t', label: 'Today', group: 'Everywhere', run: () => {} }]}>
          {open && (
            <Keys
              bindings={[
                { keys: 'a', label: 'Approve', group: 'Outbox', run: () => {}, enabled: false },
                { keys: 'mod+s', label: 'Save edits', group: 'Outbox', run: () => {}, inInputs: true },
              ]}
            />
          )}
          <Sheet />
        </Keys>
      );
    }
    const { rerender } = render(
      <HotkeysProvider>
        <Page open />
      </HotkeysProvider>,
    );
    expect(screen.getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'Everywhere: g t Today',
      'Outbox: a Approve (off)',
      'Outbox: mod+s Save edits',
    ]);

    rerender(
      <HotkeysProvider>
        <Page open={false} />
      </HotkeysProvider>,
    );
    expect(screen.getAllByRole('listitem').map((li) => li.textContent)).toEqual(['Everywhere: g t Today']);
  });

  it('shows the binding that would run when two share a key', () => {
    render(
      <HotkeysProvider>
        <Keys bindings={[{ keys: 'escape', label: 'Go back', group: 'Everywhere', run: () => {} }]}>
          <Keys bindings={[{ keys: 'escape', label: 'Leave the editor', group: 'Outbox', run: () => {} }]} />
          <Sheet />
        </Keys>
      </HotkeysProvider>,
    );
    expect(screen.getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'Outbox: escape Leave the editor',
    ]);
  });
});

describe('isTypingTarget', () => {
  it('is true for text fields and contenteditable, false otherwise', () => {
    const root = document.createElement('div');
    root.innerHTML = `
      <input id="i" /><textarea id="t"></textarea><select id="s"></select>
      <div id="ce" contenteditable="true"><b id="inner">x</b></div>
      <div id="off" contenteditable="false"></div>
      <button id="b">Go</button>
    `;
    document.body.append(root);
    const byId = (id: string) => document.getElementById(id);
    expect(isTypingTarget(byId('i'))).toBe(true);
    expect(isTypingTarget(byId('t'))).toBe(true);
    expect(isTypingTarget(byId('s'))).toBe(true);
    expect(isTypingTarget(byId('ce'))).toBe(true);
    expect(isTypingTarget(byId('inner'))).toBe(true);
    expect(isTypingTarget(byId('off'))).toBe(false);
    expect(isTypingTarget(byId('b'))).toBe(false);
    expect(isTypingTarget(document.body)).toBe(false);
    expect(isTypingTarget(document)).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
    root.remove();
  });
});
