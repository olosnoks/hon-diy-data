/**
 * The global keyboard map (main_window.py handle_key, design canvas):
 * F1-F8 screens, F9 theme Dracula <-> Darker, Space mark, Esc closes the open
 * panel (or dismisses the stall banner and acknowledges alarms). Ctrl+R start /
 * stop recording. Enter opens the stall banner's stall in F4 Replay (on F1 / F2
 * without a banner: the stall the catcher caught last).
 */
import { useEffect } from 'react';
import { SCREENS, openInReplay, useSession } from '../../core/session';

export interface KeyLike {
  key: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
  repeat?: boolean;
  target?: EventTarget | null;
}

function isTyping(target: EventTarget | null | undefined): boolean {
  if (!target || typeof (target as HTMLElement).tagName !== 'string') return false;
  const el = target as HTMLElement;
  const tag = el.tagName;
  if (tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable) return true;
  if (tag === 'INPUT') {
    const type = (el as HTMLInputElement).type;
    return !['checkbox', 'radio', 'button', 'submit', 'range', 'color'].includes(type);
  }
  return false;
}

/** Handle one key. Returns true when the key was used (the caller then
 *  prevents the browser default: F5 must not reload, Space must not scroll). */
export function handleKey(e: KeyLike): boolean {
  const s = useSession.getState();
  const fn = /^F([1-9])$/.exec(e.key);
  if (fn && !e.altKey) {
    const n = Number(fn[1]);
    if (n <= 8) s.setScreen(SCREENS[n - 1]!.id);
    else s.toggleTheme();
    return true;
  }
  const typing = isTyping(e.target);
  if (e.key === 'Escape') {
    if (typing) (e.target as HTMLElement).blur?.();
    s.escape();
    return true;
  }
  if (typing) return false; // the text field gets everything else
  if (e.key === ' ' || e.key === 'Spacebar') {
    if (!e.repeat) s.mark();
    return true;
  }
  if (e.key === 'Enter' && s.stallBanner) {
    // the stall banner is up: Enter opens that stall in Replay (desktop: any screen)
    s.openStallBanner();
    return true;
  }
  if (e.key === 'Enter' && (s.screen === 'live' || s.screen === 'drive') && s.stall.lastFile) {
    // the stall the catcher caught last opens in Replay at its trigger
    openInReplay(s.stall.lastFile, s.stall.lastT ?? 0);
    return true;
  }
  if ((e.key === 'r' || e.key === 'R') && (e.ctrlKey || e.metaKey)) {
    if (s.recording.active) s.stopRecording();
    else s.startRecording();
    return true;
  }
  return false;
}

/** Install the key map on window for the lifetime of the component. */
export function useGlobalKeys(): void {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (handleKey(e)) e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
}
