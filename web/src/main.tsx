import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import './ui/shell/fonts.css';

// The shell paints first; the data path (platform files -> hondalog.db +
// address map, the poller on a granted KKL cable or the simulator, the CYD
// companion: src/wiring/) loads in its own chunk right after, with sql.js.
// Values arrive when the link is up.
void import('./wiring')
  .then((m) => m.startApp())
  .catch((e: unknown) => {
    console.error('hondalog did not start', e);
  });
void import('./platform/pwa').then((m) => m.registerServiceWorker()).catch(() => undefined);

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
