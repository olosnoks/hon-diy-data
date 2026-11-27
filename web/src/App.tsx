/**
 * The app shell: scaling stage, top bar (F1-F8 + status), the screen under
 * it, and the global keys. Same on web, Windows, Linux and Android.
 */
import { useEffect } from 'react';
import { useSession } from './core/session';
import { FONTS } from './ui/theme';
import { useGlobalKeys } from './ui/shell/keys';
import { BottomStrip, StallBannerBar } from './ui/shell/Frame';
import { ScreenRouter } from './ui/shell/Router';
import { Stage } from './ui/shell/Stage';
import { TopBar } from './ui/shell/TopBar';
import { useTheme } from './ui/shell/useTheme';

export function App() {
  const t = useTheme();
  useGlobalKeys();
  useEffect(() => {
    document.body.style.background = t.bg;
    document.body.style.color = t.fg;
    document.documentElement.style.colorScheme = 'dark';
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', t.bg);
  }, [t]);
  return (
    <Stage background={t.bg}>
      <Shell />
    </Stage>
  );
}

function Shell() {
  const t = useTheme();
  return (
      <div
        data-theme={t.name}
        style={{ position: 'relative', width: '100%', height: '100%', display: 'flex', flexDirection: 'column', background: t.bg, color: t.fg, fontFamily: FONTS.sans }}
      >
        <TopBar />
        <ScreenRouter />
        <BottomStrip />
        <StallBannerBar />
      </div>
  );
}
