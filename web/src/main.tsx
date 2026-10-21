import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource-variable/inter';
import '@/styles/globals.css';
import { App } from '@/app/App';
import { createClient } from '@/api';

const root = document.getElementById('root');
if (!root) throw new Error('Missing #root element');

createClient().then(
  (client) =>
    createRoot(root).render(
      <StrictMode>
        <App client={client} />
      </StrictMode>,
    ),
  () => {
    // The sample data chunk did not load (server down, a deploy replaced it): reloading is the fix.
    root.textContent = 'The app did not load. Reload the page to try again.';
  },
);
