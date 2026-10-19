import { useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ClientProvider, type OutreachClient } from '@/api';

export function App({ client }: { client: OutreachClient }) {
  const [queryClient] = useState(() => new QueryClient());
  return (
    <QueryClientProvider client={queryClient}>
      <ClientProvider client={client}>
        <h1>Outreach</h1>
      </ClientProvider>
    </QueryClientProvider>
  );
}
