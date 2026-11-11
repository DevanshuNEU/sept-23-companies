import { render } from '@testing-library/react';
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
