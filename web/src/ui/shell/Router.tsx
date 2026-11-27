/**
 * The screen router. F1 Live and F2 Drive are part of the shell bundle; the
 * other screens load on first use from src/ui/<area>/index.tsx, whose default
 * export is the screen component (it fills the area under the top bar).
 */
import { Component, lazy, Suspense, type ComponentType, type ErrorInfo, type ReactNode } from 'react';
import { SCREENS, useSession, type ScreenId } from '../../core/session';
import DriveScreen from '../drive/index';
import LiveScreen from '../live/index';
import { useTheme } from './useTheme';

const LAZY: Record<Exclude<ScreenId, 'live' | 'drive'>, ComponentType> = {
  events: lazy(() => import('../events/index')),
  replay: lazy(() => import('../replay/index')),
  power: lazy(() => import('../power/index')),
  gps: lazy(() => import('../gps/index')),
  bytes: lazy(() => import('../bytes/index')),
  setup: lazy(() => import('../setup/index')),
};

export function screenComponent(id: ScreenId): ComponentType {
  if (id === 'live') return LiveScreen;
  if (id === 'drive') return DriveScreen;
  return LAZY[id];
}

function Note({ children }: { children: ReactNode }) {
  const t = useTheme();
  return (
    <div style={{ padding: 24, color: t.fg2, fontSize: 15, fontWeight: 600 }}>{children}</div>
  );
}

class ScreenBoundary extends Component<{ id: ScreenId; children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`screen ${this.props.id} failed`, error, info.componentStack);
  }
  componentDidUpdate(prev: { id: ScreenId }) {
    if (prev.id !== this.props.id && this.state.error) this.setState({ error: null });
  }
  render() {
    if (this.state.error) {
      const name = SCREENS.find((s) => s.id === this.props.id)?.name ?? this.props.id;
      return <Note>{name} stopped with an error: {this.state.error.message}. The other screens still work.</Note>;
    }
    return this.props.children;
  }
}

export function ScreenRouter() {
  const id = useSession((s) => s.screen);
  const Screen = screenComponent(id);
  return (
    <main style={{ flexGrow: 1, minHeight: 0, position: 'relative', display: 'flex', flexDirection: 'column' }}>
      <ScreenBoundary id={id}>
        <Suspense fallback={<Note>Loading…</Note>}>
          <Screen />
        </Suspense>
      </ScreenBoundary>
    </main>
  );
}
