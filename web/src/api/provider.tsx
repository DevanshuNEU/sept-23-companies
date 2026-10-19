import { createContext, useContext, type ReactNode } from 'react';
import type { OutreachClient } from './client';

const ClientContext = createContext<OutreachClient | null>(null);

export function ClientProvider({ client, children }: { client: OutreachClient; children: ReactNode }) {
  return <ClientContext.Provider value={client}>{children}</ClientContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useClient(): OutreachClient {
  const client = useContext(ClientContext);
  if (!client) throw new Error('useClient outside ClientProvider');
  return client;
}
