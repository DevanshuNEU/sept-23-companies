import { render } from '@testing-library/react';
import { fitBand } from '@/lib/fit';
import { FitChip } from './fit-chip';
import { Monogram } from './monogram';

describe('Monogram', () => {
  it('shows the first letter on a neutral tile and says nothing to a screen reader', () => {
    const { container } = render(<Monogram name="  pylon" />);
    const tile = container.firstElementChild as HTMLElement;
    expect(tile).toHaveTextContent('P');
    expect(tile).toHaveAttribute('aria-hidden', 'true');
  });

  it('has no hue of its own: every company gets the same tile', () => {
    const { container } = render(
      <>
        <Monogram name="Pylon" />
        <Monogram name="Cerebrium" />
      </>,
    );
    const [a, b] = Array.from(container.children) as HTMLElement[];
    expect(a?.className).toBe(b?.className);
    expect(a?.getAttribute('style')).toBeNull();
    expect(a?.hasAttribute('data-hue')).toBe(false);
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
  it('says the fit in a word and a number, quietly: the band only changes the weight, never a colour', () => {
    const { rerender, getByText } = render(<FitChip fit={82} />);
    expect(getByText('Fit 82')).toHaveAttribute('data-band', 'good');
    rerender(<FitChip fit={66} />);
    expect(getByText('Fit 66')).toHaveAttribute('data-band', 'mid');
    rerender(<FitChip fit={31} />);
    expect(getByText('Fit 31')).toHaveAttribute('data-band', 'low');
    for (const fit of [82, 66, 31]) {
      rerender(<FitChip fit={fit} />);
      expect(getByText(`Fit ${fit}`).className).not.toMatch(/\b(ok|warn|bad|info)\b|border-|bg-/);
    }
  });

  it('draws nothing for a company the judge has not read', () => {
    const { container } = render(<FitChip fit={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
