import { render } from '@testing-library/react';
import { fitBand } from '@/lib/fit';
import { MONOGRAM_HUES, monogramHue } from '@/lib/monogram';
import { FitChip } from './fit-chip';
import { Monogram } from './monogram';

describe('monogramHue', () => {
  it('gives a company the same hue every time, whatever the case or spacing', () => {
    expect(monogramHue('Pylon')).toBe(monogramHue('Pylon'));
    expect(monogramHue('  pylon ')).toBe(monogramHue('Pylon'));
    expect(monogramHue('PYLON')).toBe(monogramHue('Pylon'));
  });

  it('keeps today\'s picks on the hues they have had (a change here recolours every company)', () => {
    const names = ['Pylon', 'Cerebrium', 'Resend', 'Baseten', 'Inngest', 'Turso', 'Trigger.dev', 'Hex'];
    expect(names.map(monogramHue)).toEqual(['amber', 'cyan', 'slate', 'olive', 'olive', 'green', 'slate', 'green']);
  });

  it('uses only the calm palette, never the brand', () => {
    const names = Array.from({ length: 200 }, (_, i) => `Company ${i}`);
    for (const n of names) expect(MONOGRAM_HUES).toContain(monogramHue(n));
    expect(MONOGRAM_HUES).not.toContain('violet');
    expect(MONOGRAM_HUES).not.toContain('purple');
    // A spread, not one hue for everything.
    expect(new Set(names.map(monogramHue)).size).toBe(MONOGRAM_HUES.length);
  });
});

describe('Monogram', () => {
  it('shows the first letter on its hue tile and says nothing to a screen reader', () => {
    const { container } = render(<Monogram name="  pylon" />);
    const tile = container.firstElementChild as HTMLElement;
    expect(tile).toHaveTextContent('P');
    expect(tile).toHaveAttribute('aria-hidden', 'true');
    expect(tile.style.backgroundColor).toBe(`var(--mono-${monogramHue('pylon')}-bg)`);
    expect(tile.style.color).toBe(`var(--mono-${monogramHue('pylon')}-fg)`);
  });
});

describe('fitBand', () => {
  it.each([
    [100, 'good'], [82, 'good'], [70, 'good'],
    [69, 'mid'], [66, 'mid'], [50, 'mid'],
    [49, 'low'], [12, 'low'], [0, 'low'],
  ] as const)('a fit of %s is %s', (fit, band) => {
    expect(fitBand(fit)).toBe(band);
  });
});

describe('FitChip', () => {
  it('says the fit in a word and a number, coloured by its band', () => {
    const { rerender, getByText } = render(<FitChip fit={82} />);
    expect(getByText('Fit 82')).toHaveAttribute('data-band', 'good');
    rerender(<FitChip fit={66} />);
    expect(getByText('Fit 66')).toHaveAttribute('data-band', 'mid');
    rerender(<FitChip fit={31} />);
    expect(getByText('Fit 31')).toHaveAttribute('data-band', 'low');
  });

  it('draws nothing for a company the judge has not read', () => {
    const { container } = render(<FitChip fit={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
