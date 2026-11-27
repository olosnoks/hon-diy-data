/**
 * The app shell: scaling stage, top bar (F1-F8 + status), the screen under
 * it, and the global keys. Same on web, Windows, Linux and Android.
 */
import { useEffect } from 'react';
import { useSession } from './core/session';
import { FONTS } from './ui/theme';
import { useGlobalKeys } from './ui/shell/keys';
import { ScreenRouter } from './ui/shell/Router';
import { Stage } from './ui/shell/Stage';
import { TopBar } from './ui/shell/TopBar';
import { useTheme } from './ui/shell/useTheme';

export function App() {
  const t = useTheme();
  const demo = useSession((s) => s.demo);
  const simulated = useSession((s) => s.ecu.simulated && s.ecu.state !== 'stopped');
  useGlobalKeys();
  useEffect(() => {
    document.body.style.background = t.bg;
    document.body.style.color = t.fg;
    document.documentElement.style.colorScheme = 'dark';
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', t.bg);
  }, [t]);
  return (
    <Stage background={t.bg}>
      <div
        data-theme={t.name}
        style={{ position: 'relative', width: '100%', height: '100%', display: 'flex', flexDirection: 'column', background: t.bg, color: t.fg, fontFamily: FONTS.sans }}
      >
        <TopBar />
        <ScreenRouter />
        {(simulated || demo) && (
          <div data-testid="sim-badge" title="Data from the built-in simulator, not a car"
            style={{ position: 'absolute', right: 10, bottom: 34, padding: '1px 6px', fontFamily: FONTS.mono, fontSize: 11, fontWeight: 800,
              letterSpacing: 1, color: t.bg, background: t.warn, pointerEvents: 'none' }}>
            {simulated ? 'SIMULATOR' : 'DEMO DATA'}
          </div>
        )}
      </div>
    </Stage>
  );
}
