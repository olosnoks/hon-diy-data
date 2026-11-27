/**
 * Start-up: the platform's file store and port picker into the
 * AppController, the screens' dependencies registered, and the first
 * connection (a granted KKL cable, else the simulator when Setup > Cable
 * says so). main.tsx calls startApp() once.
 */
import { setExprCompiler } from '../core/analysis/filters';
import { TransportPort } from '../core/cyd/port';
import { compileExpr } from '../core/expr';
import type { DriveStore, PortInfo, Transport } from '../core/types';
import { capabilities, getFileStore, getTransportPicker } from '../platform';
import { WebSerialTransport } from '../platform/webserial';
import { registerBytesDeps } from '../ui/bytes/deps';
import type { MemLink } from '../ui/bytes/model/scanner';
import { drivesChanged } from '../ui/replay/source';
import { registerSetupDeps, type ConnectionControl } from '../ui/setup/deps';
import { alarmSourceFromEngine } from '../ui/setup/AlarmsTab';
import { cableSettings } from '../ui/setup/settings';
import { AppController } from './controller';
import { HlCydClient } from './cydclient';

/** F7 Bytes' MemLink: reads and raw frames run on the poller between its own reads. */
export function memLinkOf(app: AppController): MemLink {
  return {
    available: () => app.poller !== null && (app.poller.state === 'ok'),
    obd2Only: () => app.poller?.mode === 'obd2',
    readMem: (addr, len) => {
      const p = app.poller;
      if (!p) return Promise.reject(new Error('not connected'));
      return p.submit((link) => link.readMem(addr, len));
    },
    raw: async (frame) => {
      const p = app.poller;
      if (!p) throw new Error('not connected');
      const [echo, rest] = await p.submit((link) => link.raw(frame));
      return { echo: echo ? Uint8Array.from(frame) : new Uint8Array(0), rest };
    },
  };
}

/** Setup's drive store: an import tells Replay and Events. */
function storeForSetup(app: AppController): DriveStore {
  const s = app.store!;
  return {
    listDrives: () => s.listDrives(),
    loadDrive: (n) => s.loadDrive(n),
    deleteDrive: async (n, o) => { await s.deleteDrive(n, o); drivesChanged(); },
    importDb: async (f) => { const n = await s.importDb(f); drivesChanged(); return n; },
    exportDb: () => s.exportDb(),
  };
}

function registerDeps(app: AppController): void {
  const picker = app.opts.picker;
  const saveMap = async (text: string) => {
    await app.saveMap(text);
    registerDeps(app);      // the alarm engine and the map text are new
  };
  registerBytesDeps({
    link: memLinkOf(app),
    demo: false,
    // The poller interleaves the poke test's reads with its own: nothing to pause.
    live: { pause: async () => undefined, resume: async () => undefined },
    mapText: app.mapText,
    saveMap,
  });
  const infos = new Map<string, PortInfo>();
  const transports = new Map<string, Transport>();
  const cyd = new HlCydClient({
    listPorts: async () => {
      const ports = picker ? await picker.list() : [];
      for (const p of ports) infos.set(p.id, p);
      return ports.map((p) => ({ id: p.id, label: p.label, guess: p.guess }));
    },
    openPort: (id) => {
      const info = infos.get(id);
      if (!info || !picker) throw new Error(`unknown port ${id}`);
      const t = picker.open(info);
      transports.set(id, t);
      return new TransportPort(t, 921600);
    },
    serialPortOf: (id) => {
      const t = transports.get(id);
      return t instanceof WebSerialTransport ? t.port : null;
    },
    beforeConnect: () => app.pauseCompanion(),
    afterDisconnect: () => app.resumeCompanion(),
  });
  const connection: ConnectionControl = {
    connectPort: (port) => app.connect({ kind: 'port', port }),
    connectSim: () => app.connect({ kind: 'sim' }),
    disconnect: () => app.disconnect(),
    current: () => (app.source ? (app.source.kind === 'sim' ? 'Simulator' : app.source.port.label) : ''),
  };
  registerSetupDeps({
    alarms: app.pipeline?.alarms ? alarmSourceFromEngine(app.pipeline.alarms, () => performance.now() / 1000) : null,
    cyd,
    store: storeForSetup(app),
    picker,
    mapText: app.mapText,
    saveMap,
    platform: () => {
      const c = capabilities();
      return { platform: c.platform, serial: c.serial, files: c.files, notes: c.notes };
    },
    connection,
  });
}

/** The first connection: a granted KKL cable, else the simulator (Setup > Cable > "No cable"). */
async function firstConnect(app: AppController): Promise<void> {
  const picker = app.opts.picker;
  const cable = cableSettings();
  let port: PortInfo | undefined;
  if (picker) {
    try {
      const ports = await picker.list();
      port = ports.find((p) => p.id === cable.ecuPort) ?? ports.find((p) => p.guess === 'ecu');
    } catch {
      port = undefined;
    }
  }
  if (port) await app.connect({ kind: 'port', port });
  else if (cable.simWhenNoCable) await app.connect({ kind: 'sim' });
}

let started: AppController | null = null;

export async function startApp(): Promise<AppController> {
  if (started) return started;
  setExprCompiler((text) => compileExpr(text));
  const app = new AppController({
    files: getFileStore(),
    picker: getTransportPicker(),
    protocol: () => cableSettings().protocol,
    baud: () => cableSettings().baud,
  });
  started = app;
  await app.init();
  registerDeps(app);
  if (typeof window !== 'undefined') {
    window.addEventListener('pagehide', () => { void app.store?.persist(); });
    (window as unknown as { hondalog?: AppController }).hondalog = app;   // the console, for the owner
  }
  await firstConnect(app);
  return app;
}
