import { render, screen } from '@testing-library/react';
import { Badge } from '@/components/ui/badge';
import { buttonVariants } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { EmptyState } from '@/components/state-views';
import { Inbox } from 'lucide-react';

describe('focus ring', () => {
  it('uses ring/70 on buttons and badges, never the dim ring/50', () => {
    expect(buttonVariants()).toContain('focus-visible:ring-ring/70');
    expect(buttonVariants()).not.toContain('focus-visible:ring-ring/50');
  });
});

describe('Button default variant', () => {
  it('lifts on hover at /85 and presses darker, so it shows against a white fill in dark', () => {
    const cls = buttonVariants({ variant: 'default' });
    expect(cls).toContain('hover:bg-primary/85');
    expect(cls).toContain('active:bg-primary/75');
  });
});

describe('Tabs', () => {
  it('gives an inactive trigger a hover background', () => {
    render(
      <Tabs defaultValue="a">
        <TabsList>
          <TabsTrigger value="a">A</TabsTrigger>
          <TabsTrigger value="b">B</TabsTrigger>
        </TabsList>
      </Tabs>,
    );
    expect(screen.getByRole('tab', { name: 'B' }).className).toContain('hover:bg-background/60');
  });
});

describe('Badge chips', () => {
  it.each(['ok', 'info', 'idle'] as const)('the soft %s chip has no border colour, only fill', (variant) => {
    render(<Badge variant={variant}>x</Badge>);
    const cls = screen.getByText('x').className;
    expect(cls).not.toMatch(new RegExp(`border-${variant}-bd`));
  });

  it.each(['warn', 'bad', 'outline'] as const)('the %s chip keeps its border', (variant) => {
    render(<Badge variant={variant}>y</Badge>);
    const cls = screen.getByText('y').className;
    expect(cls).toMatch(variant === 'outline' ? /border-border/ : new RegExp(`border-${variant}-bd`));
  });
});

describe('Skeleton', () => {
  it('is a muted block with the shimmer sweep', () => {
    const { container } = render(<Skeleton />);
    const el = container.firstElementChild as HTMLElement;
    expect(el.className).toContain('bg-muted/70');
    expect(el.className).toContain('shimmer');
    expect(el.className).not.toContain('animate-pulse');
  });
});

describe('EmptyState', () => {
  it('shows a 40px muted icon tile, one line, and one key hint', () => {
    const { container } = render(
      <EmptyState icon={Inbox} title="Nothing here" hint="C">
        A line on what to do.
      </EmptyState>,
    );
    const tile = container.querySelector('[data-slot="empty-icon"]') as HTMLElement;
    expect(tile.className).toContain('size-10');
    expect(tile.className).toContain('bg-muted');
    expect(tile.className).toContain('rounded-xl');
    expect(screen.getByText('Nothing here')).toBeInTheDocument();
    expect(container.querySelectorAll('kbd')).toHaveLength(1);
    expect(container.querySelector('kbd')).toHaveTextContent('C');
  });

  it('shows no key hint unless one is given', () => {
    const { container } = render(<EmptyState icon={Inbox} title="Nothing here" />);
    expect(container.querySelector('kbd')).toBeNull();
  });
});
