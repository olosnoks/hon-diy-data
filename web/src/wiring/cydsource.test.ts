/**
 * The CYD as the ECU source (Source kind 'cyd', desktop ``cyd:`` ports): a fake CH340
 * transport streams HELLO / READ / TICK / LINK / GPS / EVENT frames built with
 * serial_proto's helpers; the app decodes the raw reads with its own map.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { loadDefault } from '../core/addressmap';
import { concat } from '../core/cyd/bytes';
import * as F from '../core/cyd/format';
import * as P from '../core/cyd/serial_proto';
import { resetSession, useSession } from '../core/session';
import { MemoryFileStore } from '../core/store/filestore';
import type { PortInfo, Transport, TransportPicker } from '../core/types';
import { AppController } from './controller';

const CH340: PortInfo = { id: 'cyd', label: 'COM22', vendorId: 0x1a86, productId: 0x7523, guess: 'cyd' };
const FTDI: PortInfo = { id: 'kkl', label: 'COM21', vendorId: 0x0403, productId: 0x6001, guess: 'ecu' };

/** A serial port: what the test pushes is read back; writes are kept. A KKL fake never answers. */
class FakeTransport implements Transport {
  readonly kind = 'webserial' as const;
  isOpen = false;
  opens = 0;
  written: Uint8Array[] = [];
  private buf: Uint8Array[] = [];
  private wake: (() => void) | null = null;
  constructor(readonly label: string) {}
  async open(): Promise<void> { this.opens += 1; this.isOpen = true; this.buf = []; }
  async close(): Promise<void> { this.isOpen = false; this.wake?.(); }
  async setBaud(): Promise<void> {}
  async setBreak(): Promise<void> {}
  async drain(): Promise<void> {}
  async write(b: Uint8Array): Promise<void> {
    if (!this.isOpen) throw new Error('closed');
    this.written.push(b);
  }
  push(b: Uint8Array): void {
    if (!this.isOpen) return;
    this.buf.push(b);
    this.wake?.();
  }
  async read(_max: number, timeoutMs: number): Promise<Uint8Array> {
    if (!this.isOpen) throw new Error('closed');
    if (!this.buf.length) {
      await new Promise<void>((resolve) => {
        const timer = setTimeout(() => { this.wake = null; resolve(); }, timeoutMs);
        this.wake = () => { clearTimeout(timer); this.wake = null; resolve(); };
      });
    }
    if (!this.isOpen) throw new Error('closed');
    const out = concat(...this.buf);
    this.buf = [];
    return out;
  }
}

/** The handheld's side: frames on its own microsecond clock. */
class CydStream {
  private seq = 0;
  private readonly devT0 = performance.now() / 1000 - 5;
  constructor(readonly t: FakeTransport) {}
  us(): number { return Math.trunc((performance.now() / 1000 - this.devT0) * 1e6); }
  frame(type: number, payload: Uint8Array): void {
    this.t.push(concat(Uint8Array.of(0), P.frameEncode(type, payload, this.seq)));
    this.seq = (this.seq + 1) & 0xff;
  }
  hello(mapHash: string, linkState: number): void {
    this.frame(P.SF_HELLO, P.packHello(P.makeHello({
      caps: P.CAP_RX, fw: '0.2.0', device: 'HH-TEST', mapHash, linkState, bootNo: 1, baud: 921600, tUs: this.us(),
    })));
  }
  read(addr: number, data: Uint8Array): void {
    this.frame(P.SF_READ, concat(P.READ.pack(this.us(), addr, data.length, 12000), data));
  }
  tick(): void { this.frame(P.SF_TICK, P.TICK.pack(this.us(), 12000, 1900, 1)); }
  link(state: number, up: boolean): void { this.frame(P.SF_LINK, P.LINK.pack(this.us(), state, up ? 1 : 0, 0, 0)); }
  gps(): void {
    this.frame(P.SF_GPS, F.REC_GPS.pack(this.us(), Math.trunc(32.27 * 1e7), Math.trunc(-90.13 * 1e7), 1_700_000_000, 1000, 9000, 1, 9, 90));
  }
  event(code: number, src = 0): void { this.frame(P.SF_EVENT, F.REC_EVENT.pack(this.us(), code, src, 0, 0)); }
}

function picker(ports: PortInfo[]) {
  const cyd = new FakeTransport(CH340.label);
  const kkl = new FakeTransport(FTDI.label);
  const opened: string[] = [];
  const p: TransportPicker & { ports: PortInfo[]; opened: string[]; cyd: FakeTransport; kkl: FakeTransport } = {
    ports, opened, cyd, kkl,
    list: async () => [...p.ports],
    open: (info) => { opened.push(info.id); return info.id === CH340.id ? cyd : kkl; },
  };
  return p;
}

async function until(pred: () => boolean, ms = 8000): Promise<void> {
  const end = Date.now() + ms;
  while (!pred()) {
    if (Date.now() > end) throw new Error('timed out');
    await new Promise((r) => setTimeout(r, 20));
  }
}

const map = loadDefault();
const rpm = map.channel('rpm')!;
const RPM_RAW = Uint8Array.from({ length: rpm.length }, (_, i) => (i === 0 ? 0x10 : 0x00));

let app: AppController | null = null;
let pump: ReturnType<typeof setInterval> | null = null;
afterEach(async () => {
  if (pump) clearInterval(pump);
  pump = null;
  await app?.close();
  app = null;
  resetSession();
});

async function cydApp(ports: PortInfo[] = [CH340], autoRecord = false) {
  const pk = picker(ports);
  app = new AppController({ files: new MemoryFileStore(), picker: pk, autoRecord, storeOptions: { flushIntervalS: 0.2, checkpointS: 0 } });
  await app.init();
  await app.autoSource();
  const cyd = new CydStream(pk.cyd);
  return { pk, cyd };
}

/** The CYD reading the ECU: READ + TICK every 50 ms. */
function stream(cyd: CydStream): void {
  pump = setInterval(() => { cyd.read(rpm.addr, RPM_RAW); cyd.tick(); }, 50);
}

describe('the CYD as the ECU source', () => {
  it('with no KKL cable a granted CYD is picked; its raw reads are decoded with our map', async () => {
    const { pk, cyd } = await cydApp();
    expect(app!.source?.kind).toBe('cyd');
    expect(app!.sourceKind).toBe('cyd');
    expect(app!.cydLink).not.toBeNull();
    expect(useSession.getState().ecu.state).toBe('connecting');
    expect(useSession.getState().channels.rpm?.value ?? null).toBeNull();     // nothing until real data
    cyd.hello(map.versionHash, P.LINK_OK);
    stream(cyd);
    await until(() => useSession.getState().channels.rpm?.fresh ?? false);
    const s = useSession.getState();
    expect(s.channels.rpm!.value).toBeCloseTo(rpm.convert(RPM_RAW)!, 6);
    expect(s.ecu.state).toBe('ok');
    expect(s.ecu.simulated).toBe(false);
    expect(s.ecu.detail).toBe('through the CYD');
    expect(s.raw[rpm.addr]).toBe(0x10);                                       // F7 Bytes' raw view
    await until(() => useSession.getState().cyd.state === 'ok');
    expect(useSession.getState().cyd.mode).toBe('standalone');                 // the top bar's CYD chip: linked
    expect(useSession.getState().cyd.mapMatch).toBe(true);
    expect(app!.linkInfo()).toMatch(/^CYD COM22 · map /);
    // one program on the CH340: no companion, no relay, opened once
    expect(app!.companion).toBeNull();
    expect(app!.relay).toBeNull();
    await app!.portPlugged();
    expect(app!.companion).toBeNull();
    expect(pk.opened).toEqual(['cyd']);
    expect(app!.poller).toBeNull();
  });

  it('LINK state 4 (no K-line cable on the CYD) shows clearly and never as data', async () => {
    const { cyd } = await cydApp();
    cyd.hello(map.versionHash, P.LINK_ERROR);
    await until(() => useSession.getState().ecu.detail === 'CYD: no K-line cable');
    let s = useSession.getState();
    expect(s.ecu.state).toBe('error');
    expect(Object.values(s.channels).every((c) => c.value === null && !c.fresh)).toBe(true);
    expect(Object.keys(s.raw).length).toBe(0);
    // data, then the cable comes off: the values go, the chip says why
    stream(cyd);
    await until(() => useSession.getState().channels.rpm?.fresh ?? false);
    clearInterval(pump!);
    pump = null;
    cyd.link(P.LINK_ERROR, false);
    await until(() => useSession.getState().ecu.detail === 'CYD: no K-line cable');
    await until(() => useSession.getState().channels.rpm?.value === null);
    s = useSession.getState();
    expect(s.channels.rpm!.fresh).toBe(false);
    expect(s.ecu.state).toBe('error');
    expect(s.cyd.state).toBe('ok');           // the CYD itself is fine
  });

  it('records the drive as source "cyd", with its GPS and events; a map mismatch is shown', async () => {
    const { cyd } = await cydApp([CH340], true);
    const gps = vi.spyOn(app!.live!, 'feedGps');
    const events = vi.spyOn(app!.live!, 'onCydEvent');
    cyd.hello('deadbeef0000', P.LINK_OK);
    stream(cyd);
    await until(() => app!.live!.recording);
    const header = app!.live!.recorder!.header;
    expect(header.source).toBe('cyd');
    expect(header.port).toBe('cyd:COM22');
    expect((header.cyd as Record<string, unknown>).map_mismatch).toBe(true);
    await until(() => useSession.getState().cyd.mapMatch === false);
    expect(useSession.getState().cyd.detail).toMatch(/address map #deadbeef0000/);
    cyd.gps();
    cyd.event(F.EV_STALL, 1);
    await until(() => gps.mock.calls.length > 0 && events.mock.calls.length > 0);
    expect(gps.mock.calls[0]![1].gps_lat).toBeCloseTo(32.27, 4);
    expect(gps.mock.calls[0]![2]).toBeUndefined();          // stream source "gps", as the desktop's CydPoller
    expect(events.mock.calls[0]![0].isStall).toBe(true);
  });

  it('a KKL cable plugged in later takes over; the CYD becomes the companion', async () => {
    const { pk, cyd } = await cydApp();
    cyd.hello(map.versionHash, P.LINK_OK);
    stream(cyd);
    await until(() => useSession.getState().channels.rpm?.fresh ?? false);
    pk.ports = [CH340, FTDI];
    await app!.portPlugged();
    expect(app!.source?.kind).toBe('port');
    expect(app!.sourceKind).toBe('kkl');
    expect(app!.cydLink).toBeNull();
    expect(app!.poller).not.toBeNull();
    await until(() => app!.companion !== null);
    expect(pk.opened).toEqual(['cyd', 'kkl', 'cyd']);
    expect(pk.cyd.isOpen || pk.cyd.opens === 2).toBe(true);
    // the KKL cable stays the source on the next look
    await app!.autoSource();
    expect(app!.source?.kind).toBe('port');
  });

  it('Setup\'s CYD tab takes the port and gives it back as the ECU source', async () => {
    const { pk, cyd } = await cydApp();
    cyd.hello(map.versionHash, P.LINK_OK);
    await until(() => pk.cyd.isOpen);
    await app!.pauseCompanion();
    expect(app!.cydLink).toBeNull();
    expect(pk.cyd.isOpen).toBe(false);
    await app!.autoSource();                  // the watchdog must not take it back meanwhile
    expect(app!.source).toBeNull();
    await app!.resumeCompanion();
    expect(app!.source?.kind).toBe('cyd');
    expect(app!.companion).toBeNull();
  });

  it('the top bar chip connects the CYD as the ECU source when no KKL cable is there', async () => {
    const pk = picker([]);
    app = new AppController({ files: new MemoryFileStore(), picker: pk, autoRecord: false });
    await app.init();
    await app.autoSource();
    expect(app.source).toBeNull();
    pk.request = async () => { pk.ports = [CH340]; return CH340; };     // the browser's chooser
    expect(await app.connectCydNow()).toBe(true);
    expect(app.source?.kind).toBe('cyd');
    expect(app.companion).toBeNull();
  });
});
