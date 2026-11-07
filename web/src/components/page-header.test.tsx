import { render, screen } from '@testing-library/react';
import { PageHeader } from './page-header';

describe('PageHeader', () => {
  it('renders the title as the page heading with the summary beneath it', () => {
    render(<PageHeader title="Jobs" summary="20 at your level in the last 30 days" />);
    expect(screen.getByRole('heading', { level: 1, name: 'Jobs' })).toBeInTheDocument();
    const summary = screen.getByText('20 at your level in the last 30 days');
    expect(summary).toHaveAttribute('aria-live', 'polite');
    expect(summary.className).toContain('text-muted-foreground');
  });

  it('puts the aside on the title row, and renders no summary line without one', () => {
    const { container } = render(<PageHeader title="Needs you" aside={<span>Boards read Mon 08:04</span>} />);
    const heading = screen.getByRole('heading', { level: 1, name: 'Needs you' });
    expect(heading.parentElement?.parentElement).toContainElement(screen.getByText('Boards read Mon 08:04'));
    expect(container.querySelector('p')).toBeNull();
  });

  it('sets the title at 20 px with tight tracking, the one size every page uses', () => {
    render(<PageHeader title="Wins" />);
    expect(screen.getByRole('heading', { level: 1 }).className).toMatch(/text-xl.*tracking-\[-0\.01em\]/);
  });
});
