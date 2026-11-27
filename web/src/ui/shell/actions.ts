/**
 * Actions the shell's top bar can run that live in the wiring (the
 * AppController): registered at start-up, absent in tests and before it.
 */
let cydConnect: (() => Promise<boolean>) | null = null;

/** The CYD chip, clicked while the CYD is off or lost: link it now. */
export function registerCydConnect(fn: (() => Promise<boolean>) | null): void {
  cydConnect = fn;
}

export function cydConnectAction(): (() => Promise<boolean>) | null {
  return cydConnect;
}
