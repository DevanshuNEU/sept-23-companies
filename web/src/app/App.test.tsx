import { render, screen } from '@testing-library/react';
import { App } from '@/app/App';
import { FixtureClient } from '@/api/fixture/client';

describe('App', () => {
  it('renders the app title', () => {
    render(<App client={new FixtureClient()} />);
    expect(screen.getByText('Outreach')).toBeInTheDocument();
  });
});
