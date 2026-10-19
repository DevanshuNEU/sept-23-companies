import { useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ClientProvider, type OutreachClient } from '@/api';

// `client` is optional until Task 4 wires createClient() in main.tsx.
export function App({ client }: { client?: OutreachClient }) {
  const [queryClient] = useState(() => new QueryClient());
  const title = <h1>Outreach</h1>;
  return (
    <QueryClientProvider client={queryClient}>
      {client ? <ClientProvider client={client}>{title}</ClientProvider> : title}
    </QueryClientProvider>
  );
}
