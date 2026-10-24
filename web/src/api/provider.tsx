import { createContext, useContext, useMemo, type ReactNode } from 'react';
import type { OutreachClient } from './client';
import { clientForPage } from './compose';
import type { ViewName } from './http/views';

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

/** The client of one page: every method on the sample side while `page` is not a live view (see `clientForPage`). */
// eslint-disable-next-line react-refresh/only-export-components
export function usePageClient(page: ViewName): OutreachClient {
  const client = useClient();
  return useMemo(() => clientForPage(client, page), [client, page]);
}
