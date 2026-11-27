import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { startApp } from './wiring';
import { registerServiceWorker } from './platform';
import './ui/shell/fonts.css';

// The real data path: platform files -> hondalog.db + address map, the
// poller on a granted KKL cable or the simulator, the CYD companion
// (src/app/). The UI renders at once; values arrive when the link is up.
void startApp().catch((e: unknown) => {
  console.error('hondalog did not start', e);
});
void registerServiceWorker();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
