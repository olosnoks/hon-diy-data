/**
 * Touch screens (the Android head unit, a phone) get no keyboard hints. The
 * Android app always counts as touch: some radios' touch panels report a
 * mouse-like pointer.
 */
import { useEffect, useState } from 'react';
import { isNativeApp } from '../../platform/native';

const QUERY = '(pointer: coarse)';

export function isTouchOnly(): boolean {
  if (typeof window === 'undefined') return false;
  return isNativeApp() || !!window.matchMedia?.(QUERY).matches;
}

export function useTouchOnly(): boolean {
  const [touch, setTouch] = useState(isTouchOnly);
  useEffect(() => {
    const mq = typeof window !== 'undefined' ? window.matchMedia?.(QUERY) : undefined;
    if (!mq) return;
    const on = () => setTouch(isTouchOnly());
    mq.addEventListener?.('change', on);
    return () => mq.removeEventListener?.('change', on);
  }, []);
  return touch;
}
