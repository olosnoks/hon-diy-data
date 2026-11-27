/**
 * F7 Bytes: raw tools and the address map together (design: "unknown bytes,
 * flags, verify, sweep"). Sub-views: Scanner (live hex grid, inspector,
 * console), Unknown bytes, Address map, Verify, Rank, Poke test.
 *
 * Keys (when no text field has focus): arrows move the selection, S sweep,
 * C clear changes, R rank view, 1-6 switch view.
 */

import { useEffect, useMemo, useState, type KeyboardEvent } from 'react';
import { FONTS, type Theme } from '../theme';
import { useTheme as useShellTheme } from '../shell/useTheme';
import { Tabs, ThemeContext } from './controls';
import { BytesDepsContext, defaultBytesDeps, type BytesDeps } from './deps';
import { MapView, tryParse } from './MapView';
import { MemoryScanner } from './model/scanner';
import { mergedEntries, type UnknownEntry } from './model/unknown';
import { PokeView } from './poke/PokeView';
import { RankView } from './RankView';
import { ByteInspector, HexConsole, HexGrid, Legend, ScannerToolbar } from './ScannerView';
import { UnknownView } from './UnknownView';
import { VerifyView } from './VerifyView';

export type BytesView = 'scanner' | 'unknown' | 'map' | 'verify' | 'rank' | 'poke';
export const VIEWS: readonly (readonly [BytesView, string])[] = [
  ['scanner', 'Scanner'], ['unknown', 'Unknown bytes'], ['map', 'Address map'], ['verify', 'Verify'], ['rank', 'Rank'], ['poke', 'Poke test'],
];

export interface BytesScreenProps {
  deps?: Partial<BytesDeps>;
  /** Defaults to the session's theme (F9). */
  theme?: Theme;
  initialView?: BytesView;
  /** Called whenever the working map text changes. */
  onMapText?: (text: string) => void;
}

/** Deps the integrator registers once (link, live control, map store); the
 *  router renders screens without props, so this is how they arrive. */
let registered: Partial<BytesDeps> = {};
export function registerBytesDeps(deps: Partial<BytesDeps>): void {
  registered = { ...registered, ...deps };
}

export default function BytesScreen(props: BytesScreenProps) {
  const sessionTheme = useShellTheme();
  const deps = useMemo<BytesDeps>(() => ({ ...defaultBytesDeps(), ...registered, ...props.deps }), [props.deps]);
  const theme = props.theme ?? sessionTheme;
  const scanner = useMemo(() => deps.scanner ?? new MemoryScanner(deps.link, deps.clock), [deps.scanner, deps.link, deps.clock]);
  useEffect(() => () => { if (!deps.scanner) scanner.setSweeping(false); }, [scanner, deps.scanner]);
  const [view, setView] = useState<BytesView>(props.initialView ?? 'scanner');
  const [sel, setSel] = useState(0x14);
  const [savedText, setSavedText] = useState(deps.mapText);
  const [mapText, setMapTextRaw] = useState(deps.mapText);
  const setMapText = (t: string) => { setMapTextRaw(t); props.onMapText?.(t); };
  useEffect(() => { setSavedText(deps.mapText); setMapTextRaw(deps.mapText); }, [deps.mapText]);
  const parsed = useMemo(() => tryParse(mapText), [mapText]);
  const [unknown, setUnknown] = useState<UnknownEntry[]>(() => mergedEntries(deps.notes));

  const onKey = (e: KeyboardEvent) => {
    const tag = (e.target as HTMLElement).tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || e.ctrlKey || e.metaKey || e.altKey) return;
    const k = e.key;
    let used = true;
    if (view === 'scanner' && k === 'ArrowLeft') setSel((s) => Math.max(0, s - 1));
    else if (view === 'scanner' && k === 'ArrowRight') setSel((s) => Math.min(255, s + 1));
    else if (view === 'scanner' && k === 'ArrowUp') setSel((s) => (s >= 16 ? s - 16 : s));
    else if (view === 'scanner' && k === 'ArrowDown') setSel((s) => (s < 240 ? s + 16 : s));
    else if (k === 's' || k === 'S') scanner.setSweeping(!scanner.sweeping);
    else if (k === 'c' || k === 'C') scanner.clearChanges();
    else if (k === 'r' || k === 'R') setView((v) => (v === 'rank' ? 'scanner' : 'rank'));
    else if (/^[1-6]$/.test(k)) setView(VIEWS[Number(k) - 1]![0]);
    else used = false;
    if (used) e.preventDefault();
  };

  return (
    <ThemeContext.Provider value={theme}>
      <BytesDepsContext.Provider value={deps}>
        <div tabIndex={0} onKeyDown={onKey} data-testid="bytes-screen"
          style={{ background: theme.bg, color: theme.fg, fontFamily: FONTS.sans, height: '100%', flex: 1, display: 'flex', flexDirection: 'column', outline: 'none', minHeight: 0 }}>
          <Tabs label="Bytes views" items={VIEWS} value={view} onChange={setView} />
          <div style={{ flex: 1, minHeight: 0, padding: 8, overflow: 'auto' }}>
            {view === 'scanner' && (
              <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 772px) minmax(280px, 1fr)', gap: 8, alignItems: 'start' }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 }}>
                  <ScannerToolbar scanner={scanner} demo={deps.demo} />
                  <HexGrid scanner={scanner} map={parsed.map} sel={sel} onSelect={setSel} />
                  <Legend />
                  <HexConsole scanner={scanner} />
                </div>
                <ByteInspector scanner={scanner} map={parsed.map} addr={sel} unknown={unknown} />
              </div>
            )}
            {view === 'unknown' && <UnknownView store={deps.notes} scanner={scanner} onPick={setSel} onChange={setUnknown} />}
            {view === 'map' && <MapView savedText={savedText} text={mapText} setText={setMapText} saveMap={deps.saveMap} onSaved={setSavedText} />}
            {view === 'verify' && <VerifyView scanner={scanner} map={parsed.map} mapText={mapText} setMapText={setMapText} clock={deps.clock} />}
            {view === 'rank' && <RankView scanner={scanner} map={parsed.map} mapText={mapText} setMapText={setMapText} onPick={setSel} />}
            {view === 'poke' && <PokeView scanner={scanner} map={parsed.map} live={deps.live} demo={deps.demo} clock={deps.clock} />}
          </div>
        </div>
      </BytesDepsContext.Provider>
    </ThemeContext.Provider>
  );
}

export { MemoryScanner, DemoMemLink } from './model/scanner';
export type { MemLink } from './model/scanner';
export type { BytesDeps } from './deps';
export type { LiveControl } from './poke/runner';
