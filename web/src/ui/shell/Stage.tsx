/**
 * The scaling layout. Every screen is drawn in design pixels (1366 x 768,
 * the ThinkPad T430). The stage scales that to the window: the inner box
 * keeps the window's aspect ratio (so it fills it, no letterbox) and its
 * shorter side maps to the design size. On a small landscape phone the
 * design is relaxed to 1140 x 640 so text stays readable.
 *
 * In the Android app the phone layout is chosen by the panel's real pixels,
 * not by CSS pixels: head units ship with all sorts of density settings, and
 * a 1280 x 720 radio at 240 dpi (853 x 480 CSS) would otherwise get the
 * cramped phone layout instead of the full one that fits its panel.
 */
import { isNativeApp } from '../../platform/native';
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { DESIGN } from '../theme';

export const PHONE = { width: 1140, height: 640 };

/** Scale and inner (design-pixel) size for a window of w x h CSS pixels.
 *  pxRatio: device pixels per CSS pixel, used only to decide on the phone layout (1 = decide by CSS pixels). */
export function stageFit(w: number, h: number, pxRatio = 1): { scale: number; width: number; height: number } {
  if (w <= 0 || h <= 0) return { scale: 1, width: DESIGN.width, height: DESIGN.height };
  let scale = Math.min(w / DESIGN.width, h / DESIGN.height);
  const r = pxRatio > 0 ? pxRatio : 1;
  if (scale * r < 0.75) scale = Math.max(scale, Math.min(w / PHONE.width, h / PHONE.height));
  return { scale, width: w / scale, height: h / scale };
}

/** The stage's inner size in design pixels (a phone in landscape: 1386 x 640, a 1024 x 600 head unit: 1365 x 800). */
export const StageSizeContext = createContext<{ width: number; height: number; scale: number }>({ width: DESIGN.width, height: DESIGN.height, scale: 1 });
export const useStageSize = () => useContext(StageSizeContext);

/** Below this design height the screens use their compact metrics (a phone in landscape). */
export const COMPACT_BELOW = 720;

function windowSize(): { w: number; h: number } {
  if (typeof window === 'undefined') return { w: DESIGN.width, h: DESIGN.height };
  return { w: window.innerWidth || DESIGN.width, h: window.innerHeight || DESIGN.height };
}

export function Stage({ background, children }: { background: string; children: ReactNode }) {
  const [size, setSize] = useState(windowSize);
  useEffect(() => {
    const onResize = () => setSize(windowSize());
    window.addEventListener('resize', onResize);
    window.addEventListener('orientationchange', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      window.removeEventListener('orientationchange', onResize);
    };
  }, []);
  const fit = stageFit(size.w, size.h, isNativeApp() ? window.devicePixelRatio || 1 : 1);
  return (
    <div style={{ position: 'fixed', inset: 0, overflow: 'hidden', background }}>
      <div
        data-testid="stage"
        style={{
          position: 'absolute', left: 0, top: 0,
          width: fit.width, height: fit.height,
          transform: `scale(${fit.scale})`, transformOrigin: '0 0',
        }}
      >
        <StageSizeContext.Provider value={fit}>{children}</StageSizeContext.Provider>
      </div>
    </div>
  );
}
