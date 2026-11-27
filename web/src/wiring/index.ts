/**
 * Start-up: the platform's file store and port picker into the
 * AppController, the screens' dependencies registered, and the first
 * connection (a granted KKL cable, else a granted CYD reading the ECU
 * itself; nothing simulated). main.tsx calls startApp() once.
 */
import { setExprCompiler } from '../core/analysis/filters';
import { TransportPort } from '../core/cyd/port';
import { compileExpr } from '../core/expr';
import type { DriveStore, PortInfo, Transport } from '../core/types';
import { capabilities, getFileStore, getTransportPicker } from '../platform';
import { WebSerialTransport } from '../platform/webserial';
import { registerBytesDeps } from '../ui/bytes/deps';
import type { MemLink } from '../ui/bytes/model/scanner';
import { harshKindAt, migrateLocalNotes } from '../ui/events/notes';
import { drivesChanged } from '../ui/replay/source';
import { registerSetupDeps, type ConnectionControl } from '../ui/setup/deps';
import { registerCydConnect } from '../ui/shell/actions';
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
    // The poke test: the poller stops its live reads (the scanner's reads still go through it), always resumed.
    live: { pause: () => app.pauseLive(), resume: () => app.resumeLive() },
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
    requestPort: async () => {
      if (!picker?.request) return null;
      // Any adapter may be picked: the chooser lists every serial port, the CH340 among them.
      const p = await picker.request([]);
      if (!p) return null;
      infos.set(p.id, p);
      return { id: p.id, label: p.label, guess: p.guess };
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
    connectPort: (port) => app.userConnect({ kind: 'port', port }),
    connectSim: () => app.userConnect({ kind: 'sim' }),
    connectCyd: async () => {
      if (app.cydHeld) throw new Error('The CYD black box tab holds the CYD: disconnect it there first.');
      if (!(await app.connectThroughCyd(true))) throw new Error('No CYD picked.');
    },
    disconnect: () => app.userDisconnect(),
    current: () => {
      const src = app.source;
      if (!src) return '';
      return src.kind === 'sim' ? 'Simulator' : src.kind === 'cyd' ? `CYD ${src.port.label}` : src.port.label;
    },
    source: () => app.sourceKind,
  };
  registerSetupDeps({
    alarms: app.pipeline?.alarms ? alarmSourceFromEngine(app.pipeline.alarms, () => performance.now() / 1000) : null,
    cyd,
    store: app.store ? storeForSetup(app) : null,
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

/** The first connection: a granted KKL cable, else a granted CYD as the ECU source
 *  (AppController.autoSource). No cable, no data: the screens stay empty until a
 *  real cable answers (the simulator is only for tests and for ?sim on a dev build). */
async function firstConnect(app: AppController): Promise<void> {
  await app.autoSource().catch((e: unknown) => { app.lastError = `connect: ${String(e)}`; });
}

/** Write hondalog.db (the writers' buffers included) when the page is hidden
 *  or goes away: a reload or a swiped-away tab loses at most the last moment. */
export function flushOnHide(app: AppController, win: Window = window, doc: Document = document): () => void {
  const flush = () => {
    const store = app.store;
    if (!store) return;
    try {
      void store.persist().catch((e: unknown) => { app.lastError = `save: ${String(e)}`; });
    } catch (e) {
      app.lastError = `save: ${String(e)}`;
    }
  };
  const onVisibility = () => { if (doc.visibilityState === 'hidden') flush(); };
  win.addEventListener('pagehide', flush);
  doc.addEventListener('visibilitychange', onVisibility);
  return () => {
    win.removeEventListener('pagehide', flush);
    doc.removeEventListener('visibilitychange', onVisibility);
  };
}

/** Serial / USB plug events: a granted port coming back picks the source again (a KKL
 *  cable plugged in takes over from the CYD) and reconnects the CYD, with no click. */
function watchPlugs(app: AppController): void {
  if (typeof navigator === 'undefined') return;
  const plugged = () => { setTimeout(() => void app.portPlugged(), 500); };   // let the CH340 settle
  const unplugged = () => { void app.portUnplugged(); };
  const nav = navigator as Navigator & { serial?: EventTarget; usb?: EventTarget };
  for (const target of [nav.serial, nav.usb]) {
    target?.addEventListener('connect', plugged);
    target?.addEventListener('disconnect', unplugged);
  }
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
    ecuPort: () => cableSettings().ecuPort,
  });
  started = app;
  // The CYD tab and the port pickers need no drive store: if the store cannot
  // open (storage blocked, another tab holding it), still register them.
  try {
    await app.init();
  } catch (e) {
    app.lastError = `start: ${(e as Error).message}`;
    console.error('hondalog: drive store did not open', e);
  }
  // Event notes kept on the device before they went into hondalog.db: moved once.
  const store = app.store;
  if (store) await migrateLocalNotes(store, globalThis.localStorage,
    async (drive, t) => harshKindAt(await store.loadDrive(drive), t)).catch((e: unknown) => { app.lastError = `notes: ${String(e)}`; });
  registerDeps(app);
  if (typeof window !== 'undefined') {
    flushOnHide(app);
    (window as unknown as { hondalog?: AppController }).hondalog = app;   // the console, for the owner
  }
  await firstConnect(app);
  // A CYD this browser was given before links by itself: now, and whenever it is plugged back in.
  if (!app.companion) void app.startCompanion();
  watchPlugs(app);
  registerCydConnect(() => app.connectCydNow());
  return app;
}
