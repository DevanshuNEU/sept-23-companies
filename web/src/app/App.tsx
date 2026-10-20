import { useState, type ReactNode } from 'react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { createBrowserRouter, RouterProvider } from 'react-router';
import { ClientProvider, type OutreachClient } from '@/api';
import { Toaster } from '@/components/ui/sonner';
import { TooltipProvider } from '@/components/ui/tooltip';
import { HotkeysProvider } from '@/keyboard/hotkeys';
import { PendingWritesProvider } from '@/pending/pending';
import { ThemeProvider, useTheme } from '@/theme/theme';
import { createQueryClient } from './query-client';
import { routes } from './routes';

function ThemedToaster() {
  const { resolved } = useTheme();
  return <Toaster theme={resolved} />;
}

/** Everything a view needs around it. Tests use this through `renderWithApp` with a memory router. */
export function AppProviders({
  client,
  queryClient,
  children,
}: {
  client: OutreachClient;
  queryClient: QueryClient;
  children: ReactNode;
}) {
  return (
    <QueryClientProvider client={queryClient}>
      <ClientProvider client={client}>
        <ThemeProvider>
          <HotkeysProvider>
            <PendingWritesProvider>
              <TooltipProvider>
                {children}
                <ThemedToaster />
              </TooltipProvider>
            </PendingWritesProvider>
          </HotkeysProvider>
        </ThemeProvider>
      </ClientProvider>
    </QueryClientProvider>
  );
}

export function App({ client }: { client: OutreachClient }) {
  const [queryClient] = useState(createQueryClient);
  const [router] = useState(() => createBrowserRouter(routes));
  return (
    <AppProviders client={client} queryClient={queryClient}>
      <RouterProvider router={router} />
    </AppProviders>
  );
}
