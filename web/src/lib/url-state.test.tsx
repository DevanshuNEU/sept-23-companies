import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, useLocation, useNavigate, useNavigationType } from 'react-router';
import { useUrlState } from '@/lib/url-state';

function Probe({ push }: { push?: boolean }) {
  const [tab, setTab] = useUrlState('tab', 'to_reach', { push });
  const loc = useLocation();
  const navigate = useNavigate();
  const type = useNavigationType();
  return (
    <div>
      <output data-testid="value">{tab}</output>
      <output data-testid="search">{loc.search}</output>
      <output data-testid="type">{type}</output>
      <button onClick={() => setTab('waiting')}>waiting</button>
      <button onClick={() => setTab('to_reach')}>initial</button>
      <button onClick={() => navigate('/?other=1')}>other</button>
    </div>
  );
}

const show = (id: string) => screen.getByTestId(id).textContent;

describe('useUrlState', () => {
  it('reads the initial value without writing it to the URL', () => {
    render(<MemoryRouter initialEntries={['/']}><Probe /></MemoryRouter>);
    expect(show('value')).toBe('to_reach');
    expect(show('search')).toBe('');
  });

  it('reads the value from the URL', () => {
    render(<MemoryRouter initialEntries={['/?tab=replied']}><Probe /></MemoryRouter>);
    expect(show('value')).toBe('replied');
  });

  it('replaces the history entry by default', () => {
    render(<MemoryRouter initialEntries={['/']}><Probe /></MemoryRouter>);
    fireEvent.click(screen.getByText('waiting'));
    expect(show('value')).toBe('waiting');
    expect(show('search')).toBe('?tab=waiting');
    expect(show('type')).toBe('REPLACE');
  });

  it('pushes a history entry when asked', () => {
    render(<MemoryRouter initialEntries={['/']}><Probe push /></MemoryRouter>);
    fireEvent.click(screen.getByText('waiting'));
    expect(show('type')).toBe('PUSH');
  });

  it('removes the param when set back to the initial value', () => {
    render(<MemoryRouter initialEntries={['/?tab=waiting']}><Probe /></MemoryRouter>);
    fireEvent.click(screen.getByText('initial'));
    expect(show('value')).toBe('to_reach');
    expect(show('search')).toBe('');
  });

  it('keeps other params', () => {
    render(<MemoryRouter initialEntries={['/?q=graphite']}><Probe /></MemoryRouter>);
    fireEvent.click(screen.getByText('waiting'));
    expect(show('search')).toBe('?q=graphite&tab=waiting');
  });
});
