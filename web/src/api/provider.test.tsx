import { renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import type { OutreachClient } from '@/api/client';
import { ClientProvider, useClient } from '@/api/provider';

describe('useClient', () => {
  it('throws outside ClientProvider', () => {
    const quiet = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => renderHook(() => useClient())).toThrow('useClient outside ClientProvider');
    quiet.mockRestore();
  });

  it('returns the given client inside ClientProvider', () => {
    const client = { source: 'sample' } as OutreachClient;
    const wrapper = ({ children }: { children: ReactNode }) => (
      <ClientProvider client={client}>{children}</ClientProvider>
    );
    const { result } = renderHook(() => useClient(), { wrapper });
    expect(result.current).toBe(client);
  });
});
