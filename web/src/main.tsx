import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { startDemo } from './ui/shell/demo';
import './ui/shell/fonts.css';

// Until the link / simulator modules feed the session store, the demo data
// source drives the UI. Remove this (or gate it on "no transport") then.
startDemo();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
