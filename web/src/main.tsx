import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource-variable/inter';
import '@/styles/globals.css';
import { App } from '@/app/App';
import { createClient } from '@/api';

const root = document.getElementById('root');
if (!root) throw new Error('Missing #root element');

createRoot(root).render(
  <StrictMode>
    <App client={createClient()} />
  </StrictMode>,
);
