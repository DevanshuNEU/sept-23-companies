import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ThemeProvider, useTheme } from './theme';

const KEY = 'outreach.theme';

type Listener = (e: { matches: boolean }) => void;

// Replace the setup stub with a controllable system preference.
function stubSystem(initialDark: boolean) {
  let dark = initialDark;
  const listeners = new Set<Listener>();
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    get matches() {
      return query === '(prefers-color-scheme: dark)' ? dark : false;
    },
    media: query,
    addEventListener: (_: string, l: Listener) => listeners.add(l),
    removeEventListener: (_: string, l: Listener) => listeners.delete(l),
  })) as unknown as typeof window.matchMedia;
  return {
    set(next: boolean) {
      dark = next;
      act(() => listeners.forEach((l) => l({ matches: next })));
    },
    listenerCount: () => listeners.size,
  };
}

function Probe() {
  const { theme, resolved, setTheme, toggle } = useTheme();
  return (
    <div>
      <p data-testid="theme">{theme}</p>
      <p data-testid="resolved">{resolved}</p>
      <button onClick={toggle}>toggle</button>
      <button onClick={() => setTheme('system')}>system</button>
      <button onClick={() => setTheme('light')}>light</button>
    </div>
  );
}

function renderProbe() {
  return render(
    <ThemeProvider>
      <Probe />
    </ThemeProvider>,
  );
}

const isDark = () => document.documentElement.classList.contains('dark');

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.classList.remove('dark');
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('theme', () => {
  it('follows the system setting by default', () => {
    const system = stubSystem(true);
    renderProbe();
    expect(screen.getByTestId('theme')).toHaveTextContent('system');
    expect(screen.getByTestId('resolved')).toHaveTextContent('dark');
    expect(isDark()).toBe(true);

    system.set(false);
    expect(screen.getByTestId('resolved')).toHaveTextContent('light');
    expect(isDark()).toBe(false);
  });

  it('toggle stores the choice', async () => {
    stubSystem(false);
    renderProbe();
    await userEvent.click(screen.getByText('toggle'));
    expect(screen.getByTestId('theme')).toHaveTextContent('dark');
    expect(screen.getByTestId('resolved')).toHaveTextContent('dark');
    expect(window.localStorage.getItem(KEY)).toBe('dark');
    expect(isDark()).toBe(true);

    await userEvent.click(screen.getByText('toggle'));
    expect(screen.getByTestId('theme')).toHaveTextContent('light');
    expect(window.localStorage.getItem(KEY)).toBe('light');
    expect(isDark()).toBe(false);
  });

  it('a stored choice wins over the system', () => {
    const system = stubSystem(true);
    window.localStorage.setItem(KEY, 'light');
    renderProbe();
    expect(screen.getByTestId('theme')).toHaveTextContent('light');
    expect(screen.getByTestId('resolved')).toHaveTextContent('light');
    expect(isDark()).toBe(false);

    system.set(true);
    expect(screen.getByTestId('resolved')).toHaveTextContent('light');
  });

  it('an invalid stored value means system', () => {
    stubSystem(true);
    window.localStorage.setItem(KEY, 'purple');
    renderProbe();
    expect(screen.getByTestId('theme')).toHaveTextContent('system');
    expect(screen.getByTestId('resolved')).toHaveTextContent('dark');
  });

  it('survives localStorage that throws', async () => {
    stubSystem(false);
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    renderProbe();
    expect(screen.getByTestId('theme')).toHaveTextContent('system');
    await userEvent.click(screen.getByText('toggle'));
    expect(screen.getByTestId('theme')).toHaveTextContent('dark');
    expect(isDark()).toBe(true);
  });

  it('choosing system again follows the system and stores that', async () => {
    stubSystem(true);
    window.localStorage.setItem(KEY, 'light');
    renderProbe();
    await userEvent.click(screen.getByText('system'));
    expect(screen.getByTestId('theme')).toHaveTextContent('system');
    expect(screen.getByTestId('resolved')).toHaveTextContent('dark');
    expect(window.localStorage.getItem(KEY)).toBe('system');
  });

  it('stops listening to the system on unmount', () => {
    const system = stubSystem(false);
    const { unmount } = renderProbe();
    expect(system.listenerCount()).toBe(1);
    unmount();
    expect(system.listenerCount()).toBe(0);
  });
});
