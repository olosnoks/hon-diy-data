/**
 * F8 Setup (design: "Alarms, the CYD black box (map push, SD drives,
 * firmware), units, cable and map files"). Tabs: Alarms, CYD black box,
 * Units, Cable, Files, Theme, About.
 *
 * The router renders screens without props, so the integrator registers
 * what the tabs need once with registerSetupDeps (alarm engine, CYD client,
 * drive store, map store); everything has a safe default.
 */

import { useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import type { DriveStore, PortInfo, TransportPicker } from '../../core/types';
import { useSession } from '../../core/session';
import { DARKER, DRACULA, FONTS, type Theme } from '../theme';
import { useTheme as useShellTheme } from '../shell/useTheme';
import { Button, Card, Check, downloadFile, mono, Note, readFileAsBytes, Select, Tabs, ThemeContext, useTheme } from '../bytes/controls';
import { SHIPPED_MAP } from '../bytes/deps';
import { tryParse } from '../bytes/MapView';
import { validateMap } from '../bytes/model/mapdoc';
import { AlarmsTab, type AlarmSource } from './AlarmsTab';
import type { CydClient } from './cyd/client';
import { CydTab, type Confirm } from './cyd/CydTab';
import { useCable, type Protocol } from './settings';
import { registeredSetupDeps, setupDepsVersion, subscribeSetupDeps, type ConnectionControl, type PlatformFacts, type SetupDeps } from './deps';
import pkg from '../../../package.json';

export type SetupTab = 'alarms' | 'cyd' | 'units' | 'cable' | 'files' | 'theme' | 'about';
export const TABS: readonly (readonly [SetupTab, string])[] = [
  ['alarms', 'Alarms'], ['cyd', 'CYD black box'], ['units', 'Units'], ['cable', 'Cable'], ['files', 'Files'], ['theme', 'Theme'], ['about', 'About'],
];

export type { ConnectionControl, PlatformFacts, SetupDeps } from './deps';
export { registerSetupDeps } from './deps';

function defaultPlatform(): PlatformFacts {
  return { platform: 'web', serial: typeof navigator !== 'undefined' && 'serial' in navigator ? 'webserial' : 'none', files: 'unknown', notes: [] };
}

export function defaultSetupDeps(): SetupDeps {
  return {
    alarms: null, cyd: null, store: null, picker: null, mapText: SHIPPED_MAP, saveMap: null, platform: defaultPlatform,
    versions: { app: pkg.version }, confirm: (text) => window.confirm(text), connection: null,
  };
}

function Row(props: { label: string; children: ReactNode }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '220px 1fr', gap: 12, alignItems: 'center', marginBottom: 10 }}>
      <span style={{ fontWeight: 700 }}>{props.label}</span><div>{props.children}</div>
    </div>
  );
}

function UnitsTab() {
  const units = useSession((s) => s.units);
  const setUnits = useSession((s) => s.setUnits);
  return (
    <Card title="Units" style={{ maxWidth: 760 }}>
      <Row label="Display units">
        <div style={{ display: 'flex', gap: 8 }}>
          <Button active={units === 'imperial'} onClick={() => setUnits('imperial')}>US (°F, mph, mi, inHg, gal/h)</Button>
          <Button active={units === 'metric'} onClick={() => setUnits('metric')}>Metric (°C, km/h, kPa, L/h)</Button>
        </div>
      </Row>
      <Note>Only the screens change: logs, the database and exports stay metric, as in the Python app. The CYD has its own `units` line in HANDHELD.CFG (CYD black box &gt; Config).</Note>
    </Card>
  );
}

/** The simulator is for development only: shown with ?sim in the address. The owner's app never shows made-up data. */
function devSim(): boolean {
  try { return new URLSearchParams(window.location.search).has('sim'); } catch { return false; }
}

const SOURCE_TEXT: Record<'kkl' | 'cyd' | 'sim' | '', string> = {
  kkl: 'KKL cable', cyd: 'through the CYD', sim: 'simulator (dev)', '': 'not connected',
};

function ConnectionCard(props: { connection: ConnectionControl; picker: TransportPicker | null; ports: PortInfo[]; onPorts: (p: PortInfo[]) => void }) {
  const t = useTheme();
  const ecu = useSession((s) => s.ecu);
  const cable = useCable();
  const [msg, setMsg] = useState('');
  const run = async (f: () => Promise<void>) => {
    setMsg('');
    try { await f(); } catch (e) { setMsg((e as Error).message); }
  };
  const connectCable = () => run(async () => {
    let ports = props.ports.length ? props.ports : props.picker ? await props.picker.list() : [];
    let port = ports.find((p) => p.id === cable.ecuPort) ?? ports.find((p) => p.guess === 'ecu');
    if (!port && props.picker?.request) {
      const asked = await props.picker.request();
      if (asked) { ports = [...ports.filter((p) => p.id !== asked.id), asked]; port = asked; }
    }
    props.onPorts(ports);
    if (!port) { setMsg(props.picker ? 'No KKL cable picked.' : 'No serial access on this platform: use the simulator.'); return; }
    cable.set({ ecuPort: port.id });
    await props.connection.connectPort(port);
  });
  const current = props.connection.current();
  const source = props.connection.source?.() ?? (current ? 'kkl' : '');
  const connectCyd = props.connection.connectCyd;
  return (
    <Card title="Connection" style={{ maxWidth: 860, marginBottom: 8 }}>
      <div style={{ ...mono, marginBottom: 4 }} data-testid="connection-source">
        {`Source: ${SOURCE_TEXT[source]}`}
      </div>
      <div style={{ ...mono, marginBottom: 10 }} data-testid="connection-state">
        {current ? `${current}: ${ecu.state}${ecu.detail ? ` (${ecu.detail})` : ''}` : 'not connected'}
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <Button onClick={() => void connectCable()} disabled={!props.picker}>Connect the KKL cable</Button>
        {connectCyd && <Button onClick={() => void run(() => connectCyd())} disabled={!props.picker}>Connect through the CYD</Button>}
        {devSim() && <Button onClick={() => void run(() => props.connection.connectSim())}>Simulator (dev)</Button>}
        <Button onClick={() => void run(() => props.connection.disconnect())} disabled={!current}>Disconnect</Button>
      </div>
      <Note>With no KKL cable on the laptop the app reads the ECU through the CYD (its own K-line cable, over its USB-C); a KKL cable plugged in takes over.</Note>
      <Note>An OBD2 (ISO 9141-2) slow init takes about 3 s: keep hondalog in front while it connects (a hidden tab slows its timers and the init fails).</Note>
      {msg && <Note style={{ color: t.alarm }}>{msg}</Note>}
    </Card>
  );
}

function CableTab(props: { picker: TransportPicker | null; notes: string[]; connection: ConnectionControl | null }) {
  const t = useTheme();
  const cable = useCable();
  const [ports, setPorts] = useState<PortInfo[]>([]);
  const [msg, setMsg] = useState('');
  const list = async () => {
    if (!props.picker) { setMsg('No serial access on this platform: the simulator still works.'); return; }
    try { setPorts(await props.picker.list()); setMsg(''); } catch (e) { setMsg((e as Error).message); }
  };
  const request = async () => {
    if (!props.picker?.request) return;
    try {
      const p = await props.picker.request();
      if (p) {
        setPorts((xs) => [...xs.filter((x) => x.id !== p.id), p]);
        if (p.guess === 'cyd') cable.set({ cydPort: p.id }); else cable.set({ ecuPort: p.id });
      }
    } catch (e) { setMsg((e as Error).message); }
  };
  const opts = (current: string, prefer: 'ecu' | 'cyd') => [
    ['', prefer === 'ecu' ? 'Ask / first FTDI (KKL)' : 'Ask / first CH340 (CYD)'] as const,
    ...ports.map((p) => [p.id, `${p.label}${p.guess !== 'unknown' ? `  (${p.guess === 'ecu' ? 'KKL cable' : 'CYD'})` : ''}`] as const),
    ...(current && !ports.some((p) => p.id === current) ? [[current, `${current} (last used)`] as const] : []),
  ];
  return (
    <>
    {props.connection && <ConnectionCard connection={props.connection} picker={props.picker} ports={ports} onPorts={setPorts} />}
    <Card title="Cable and ports" style={{ maxWidth: 860 }}>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <Button onClick={() => void list()}>List ports</Button>
        {props.picker?.request && <Button onClick={() => void request()}>Add a port…</Button>}
      </div>
      <Row label="ECU (KKL cable)"><Select aria-label="ECU port" value={cable.ecuPort} onChange={(v) => cable.set({ ecuPort: v })} options={opts(cable.ecuPort, 'ecu')} style={{ minWidth: 360 }} /></Row>
      <Row label="CYD black box (USB-C)"><Select aria-label="CYD port" value={cable.cydPort} onChange={(v) => cable.set({ cydPort: v })} options={opts(cable.cydPort, 'cyd')} style={{ minWidth: 360 }} /></Row>
      <Row label="Baud">
        <Select aria-label="baud" value={String(cable.baud)} onChange={(v) => cable.set({ baud: Number(v) as 0 | 9600 | 10400 })}
          options={[['0', 'From the address map (10400 on the P2P)'], ['10400', '10400 (with the wake frame)'], ['9600', '9600 (kerpz P2T)']]} />
      </Row>
      <Row label="Protocol">
        <Select aria-label="protocol" value={cable.protocol} onChange={(v: Protocol) => cable.set({ protocol: v })}
          options={[['auto', 'Auto: Honda reads, OBD2 (ISO 9141-2) if they stay unanswered'], ['honda', 'Honda reads only'], ['obd2', 'OBD2 (ISO 9141-2) only']]} />
      </Row>
      <Button onClick={cable.reset}>Defaults</Button>
      {cable.ecuPort && cable.ecuPort === cable.cydPort && <Note style={{ color: t.warn }}>The ECU and the CYD cannot share a port.</Note>}
      {msg && <Note>{msg}</Note>}
      {props.notes.map((n) => <Note key={n}>{n}</Note>)}
      <Note>The CYD is the black box: when it relays, the ECU frames come through its USB-C and the KKL cable is not needed.</Note>
    </Card>
    </>
  );
}

function FilesTab(props: { store: DriveStore | null; mapText: string; saveMap: ((t: string) => Promise<void>) | null; confirm: Confirm }) {
  const t = useTheme();
  const [msg, setMsg] = useState('');
  const dbInput = useRef<HTMLInputElement>(null);
  const mapInput = useRef<HTMLInputElement>(null);
  const map = tryParse(props.mapText).map;
  const exportDb = async () => {
    if (!props.store) return;
    try {
      const data = await props.store.exportDb();
      downloadFile('hondalog.db', data);
      setMsg(`hondalog.db exported (${(data.length / 1048576).toFixed(1)} MB)`);
    } catch (e) { setMsg((e as Error).message); }
  };
  const importDb = async (f: File | undefined) => {
    if (!f || !props.store) return;
    try { const n = await props.store.importDb(await readFileAsBytes(f)); setMsg(`${n} drives imported from ${f.name}`); } catch (e) { setMsg(`not imported: ${(e as Error).message}`); }
  };
  const loadMap = async (f: File | undefined, text?: string) => {
    const body = text ?? (f ? new TextDecoder().decode(await readFileAsBytes(f)) : '');
    const problems = validateMap(body).filter((p) => !p.startsWith('note:'));
    if (problems.length) { setMsg(`NO  ${problems[0]}`); return; }
    const m = tryParse(body).map!;
    if (!props.saveMap) { setMsg(`OK  ${m.version} #${m.hash}, but this build has no map store to save it to.`); return; }
    if (!props.confirm(`Use ${m.version} (#${m.hash}) as the address map from now on?`)) return;
    try {
      await props.saveMap(body);
      setMsg(`OK  ${m.version} #${m.hash} is the address map now. Push it to the CYD (CYD black box > Map) so the hashes match.`);
    } catch (e) { setMsg((e as Error).message); }
  };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 860 }}>
      <Card title="Drive database (hondalog.db)">
        <Note style={{ marginTop: 0 }}>The same SQLite file as the Python app: drives move between the two.</Note>
        <div style={{ display: 'flex', gap: 8 }}>
          <Button disabled={!props.store} onClick={() => void exportDb()}>Export hondalog.db</Button>
          <Button disabled={!props.store} onClick={() => dbInput.current?.click()}>Import a hondalog.db…</Button>
          <input ref={dbInput} type="file" hidden accept=".db,.sqlite" aria-label="database to import" onChange={(e) => { void importDb(e.target.files?.[0]); e.target.value = ''; }} />
        </div>
        {!props.store && <Note>The drive store is not connected in this build.</Note>}
      </Card>
      <Card title="Address map files">
        <div style={{ ...mono, marginBottom: 8 }} data-testid="map-in-use">
          {map ? `in use: ${map.version} #${map.hash} (${map.variant || 'no variant'}, ${map.channels.length} channels)` : 'the map in use does not load'}
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Button onClick={() => downloadFile('addressmap.toml', props.mapText, 'text/plain')}>Export addressmap.toml</Button>
          <Button onClick={() => mapInput.current?.click()}>Load a map file…</Button>
          <Button onClick={() => void loadMap(undefined, SHIPPED_MAP)}>Back to the shipped map</Button>
          <input ref={mapInput} type="file" hidden accept=".toml" aria-label="map file to load" onChange={(e) => { void loadMap(e.target.files?.[0]); e.target.value = ''; }} />
        </div>
        <Note>Edit the map on F7 Bytes &gt; Address map. A map is checked before it is used; the version hash goes into every drive and the CYD compares it.</Note>
      </Card>
      {msg && <Note style={{ color: msg.startsWith('NO') ? t.alarm : t.fg }}>{msg}</Note>}
    </div>
  );
}

function ThemeTab() {
  const theme = useSession((s) => s.theme);
  const setTheme = useSession((s) => s.setTheme);
  const swatch = (th: Theme, name: 'dracula' | 'darker', title: string) => (
    <button key={name} onClick={() => setTheme(name)} aria-pressed={theme === name}
      style={{ background: th.bg, color: th.fg, border: `${theme === name ? 4 : 2}px solid ${theme === name ? th.accent : th.rule}`, padding: 14, width: 280,
        textAlign: 'left', cursor: 'pointer', borderRadius: 0, fontFamily: FONTS.sans }}>
      <div style={{ fontWeight: 800, fontSize: 16 }}>{title}{theme === name ? '  ✓' : ''}</div>
      <div style={{ display: 'flex', gap: 4, marginTop: 8 }}>
        {[th.panel, th.sunk, th.accent, th.warn, th.alarm, th.ok, ...th.series].map((c, i) => <span key={i} style={{ width: 22, height: 22, background: c, border: `1px solid ${th.line}` }} />)}
      </div>
    </button>
  );
  return (
    <Card title="Theme" style={{ maxWidth: 760 }}>
      <div style={{ display: 'flex', gap: 12 }}>{swatch(DRACULA, 'dracula', 'Dracula')}{swatch(DARKER, 'darker', 'Darker')}</div>
      <Note>F9 switches anywhere. There are only dark themes: no white screens. The CYD uses the same palette.</Note>
    </Card>
  );
}

function AboutTab(props: { versions: Record<string, string>; platform: PlatformFacts; mapText: string }) {
  const map = tryParse(props.mapText).map;
  const cyd = useSession((s) => s.cyd);
  const rows: [string, string][] = [
    ...Object.entries(props.versions).map(([k, v]) => [k === 'app' ? 'hondalog (web)' : k, v] as [string, string]),
    ['platform', `${props.platform.platform} · serial ${props.platform.serial} · files ${props.platform.files}`],
    ['address map', map ? `${map.version} #${map.hash}` : 'does not load'],
    ['CYD firmware', cyd.hello ? `${cyd.hello.fw} (${cyd.hello.variant}, map #${cyd.hello.mapHash})` : 'not connected'],
  ];
  return (
    <Card title="About" style={{ maxWidth: 760 }}>
      {rows.map(([k, v]) => <Row key={k} label={k}><span style={mono}>{v}</span></Row>)}
      {props.platform.notes.map((n) => <Note key={n}>{n}</Note>)}
      <Note>No network at runtime except cached OpenStreetMap tiles on the GPS map. No analytics. No GPS data leaves the device.</Note>
    </Card>
  );
}

export interface SetupScreenProps { deps?: Partial<SetupDeps>; theme?: Theme; initialTab?: SetupTab }

export default function SetupScreen(props: SetupScreenProps) {
  const sessionTheme = useShellTheme();
  const theme = props.theme ?? sessionTheme;
  const depsVersion = useSyncExternalStore(subscribeSetupDeps, setupDepsVersion);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const deps = useMemo<SetupDeps>(() => ({ ...defaultSetupDeps(), ...registeredSetupDeps(), ...props.deps }), [props.deps, depsVersion]);
  const [tab, setTab] = useState<SetupTab>(props.initialTab ?? 'alarms');
  const [mapText, setMapText] = useState(deps.mapText);
  const saveMap = deps.saveMap ? async (t: string) => { await deps.saveMap!(t); setMapText(t); } : null;
  const ecuPort = useCable((s) => s.ecuPort);
  const platform = useMemo(() => { try { return deps.platform(); } catch { return defaultPlatform(); } }, [deps]);
  const map = tryParse(mapText).map;
  const setCyd = useSession((s) => s.setCyd);
  return (
    <ThemeContext.Provider value={theme}>
      <div data-testid="setup-screen" style={{ background: theme.bg, color: theme.fg, fontFamily: FONTS.sans, height: '100%', flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <Tabs label="Setup tabs" items={TABS} value={tab} onChange={setTab} />
        <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: 8 }}>
          {tab === 'alarms' && <AlarmsTab source={deps.alarms} mapText={mapText} />}
          {tab === 'cyd' && (
            <CydTab client={deps.cyd} mapText={mapText} mapLabel={map ? `${map.version} #${map.hash}` : 'the map does not load'} confirm={deps.confirm} ecuPort={ecuPort}
              onHello={(h) => setCyd({ mapHash: h.map, mapMatch: map && h.map ? h.map === map.hash : null })} />
          )}
          {tab === 'units' && <UnitsTab />}
          {tab === 'cable' && <CableTab picker={deps.picker} notes={platform.notes} connection={deps.connection} />}
          {tab === 'files' && <FilesTab store={deps.store} mapText={mapText} saveMap={saveMap} confirm={deps.confirm} />}
          {tab === 'theme' && <ThemeTab />}
          {tab === 'about' && <AboutTab versions={deps.versions} platform={platform} mapText={mapText} />}
        </div>
      </div>
    </ThemeContext.Provider>
  );
}

export type { AlarmSource } from './AlarmsTab';
export { alarmSourceFromEngine } from './AlarmsTab';
export type { CydClient } from './cyd/client';
