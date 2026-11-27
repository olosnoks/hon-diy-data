/**
 * PWA glue: register the service worker (public/sw.js) so the app works
 * offline after the first visit, in the browser, as an installed PWA and in
 * the Android TWA. Call registerServiceWorker() once from main.tsx.
 */

// tsconfig does not load vite/client types; read Vite's env through a narrow view.
const env = (import.meta as unknown as { env?: { DEV?: boolean; BASE_URL?: string } }).env ?? {};

export async function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return null;
  // The dev server serves modules that must not be cached.
  if (env.DEV) return null;
  const base = env.BASE_URL ?? '/';
  try {
    return await navigator.serviceWorker.register(`${base}sw.js`, { scope: base });
  } catch {
    return null; // file:// or blocked: the app still runs, just not offline
  }
}

const TWA_KEY = 'hondalog.twa';

/** Opened by the Android APK (Trusted Web Activity). Remembered for the tab's life,
 *  since the android-app:// referrer is only there on the first page load. */
export function isTwa(): boolean {
  if (typeof document === 'undefined') return false;
  try {
    if (document.referrer.startsWith('android-app://com.olosnoks.hondalog')) {
      sessionStorage.setItem(TWA_KEY, '1');
      return true;
    }
    return sessionStorage.getItem(TWA_KEY) === '1';
  } catch {
    return false;
  }
}

/** Running as an installed app (PWA window or TWA) rather than a browser tab. */
export function isStandalone(): boolean {
  if (typeof window === 'undefined') return false;
  if (isTwa()) return true;
  const mm = (q: string) => (typeof window.matchMedia === 'function' ? window.matchMedia(q).matches : false);
  return mm('(display-mode: standalone)') || mm('(display-mode: fullscreen)');
}
