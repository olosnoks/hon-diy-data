/**
 * Setup > CYD black box's CydClient (src/ui/setup/cyd/client.ts) over #5's
 * HL text session (HlClient) on the CH340 port, map push (buildMapFromToml
 * + HL PUT /HANDHELD.MAP) and the firmware install (flash.ts + esptool-js
 * over the Web Serial SerialPort). While the tab holds the port the live
 * companion lets go of it (pause/resume hooks).
 */
import { loads } from '../core/addressmap';
import { esptoolLoaderFactory, flash, type FlashProgress } from '../core/cyd/flash';
import { HlClient, mapFolderFiles } from '../core/cyd/hl_client';
import { buildMapFromToml } from '../core/cyd/mapfile';
import type { Clock, CydPort } from '../core/cyd/port';
import type { CydClient, CydPortChoice, CydState, Progress } from '../ui/setup/cyd/client';

export interface CydClientOptions {
  /** Ports to offer (the platform picker's granted ports). */
  listPorts(): Promise<CydPortChoice[]>;
  /** The browser's port chooser (from a click): a port not granted yet. */
  requestPort?(): Promise<CydPortChoice | null>;
  /** Open a port by id as a CydPort (TransportPort over the picker's Transport). */
  openPort(id: string): CydPort;
  /** The W3C SerialPort behind a port id, for esptool-js (null: WebUSB, no flashing). */
  serialPortOf?(id: string): unknown | null;
  /** Called before the tab takes the port and after it lets go (the live companion). */
  beforeConnect?(): Promise<void>;
  afterDisconnect?(): Promise<void>;
  clock?: Clock;
}

export class HlCydClient implements CydClient {
  private port: CydPort | null = null;
  private portId = '';
  private hl: HlClient | null = null;
  private consoles = new Set<(line: string) => void>();
  private states = new Set<(state: CydState, detail: string) => void>();

  constructor(readonly opts: CydClientOptions) {}

  async ports(): Promise<CydPortChoice[]> {
    const ports = await this.opts.listPorts();
    return [...ports].sort((a, b) => Number(b.guess === 'cyd') - Number(a.guess === 'cyd'));
  }

  async requestPort(): Promise<CydPortChoice | null> {
    return this.opts.requestPort ? this.opts.requestPort() : null;
  }

  private emitState(state: CydState, detail = ''): void {
    for (const f of this.states) f(state, detail);
  }

  private client(): HlClient {
    if (!this.hl) throw new Error('not connected');
    this.hl.cancelFlag = false;
    return this.hl;
  }

  async connect(portId: string): Promise<Record<string, string>> {
    await this.disconnect();
    await this.opts.beforeConnect?.();
    this.emitState('connecting', portId);
    const port = this.opts.openPort(portId);
    try {
      await port.open();
      const hl = new HlClient(port, {
        onConsole: (t) => { for (const f of this.consoles) f(t); },
        onState: (open) => { if (!open && this.hl === hl) this.emitState('connected', 'session idle'); },
        ...(this.opts.clock ? { clock: this.opts.clock } : {}),
      });
      const hello = await hl.connect();
      const info = await hl.hello();
      this.port = port;
      this.portId = portId;
      this.hl = hl;
      this.emitState('connected', hello.fw);
      return { fw: hello.fw, id: hello.device, map: hello.mapHash, ...info };
    } catch (e) {
      await port.close().catch(() => undefined);
      await this.opts.afterDisconnect?.();
      this.emitState('disconnected', (e as Error).message);
      throw e;
    }
  }

  async disconnect(): Promise<void> {
    const hl = this.hl;
    const port = this.port;
    if (!hl && !port) return;
    this.hl = null;
    this.port = null;
    await hl?.close().catch(() => undefined);
    await port?.close().catch(() => undefined);
    this.emitState('disconnected');
    await this.opts.afterDisconnect?.();
  }

  onState(fn: (state: CydState, detail: string) => void): () => void {
    this.states.add(fn);
    return () => this.states.delete(fn);
  }

  onConsole(fn: (line: string) => void): () => void {
    this.consoles.add(fn);
    return () => this.consoles.delete(fn);
  }

  sendLine(text: string): void {
    void this.port?.write(new TextEncoder().encode(text + '\n'));
  }

  stat() { return this.client().stat(); }
  ls(path: string) { return this.client().ls(path); }
  get(path: string, progress?: Progress) { return this.client().get(path, progress); }
  put(path: string, data: Uint8Array, progress?: Progress) { return this.client().put(path, data, progress); }
  rm(path: string) { return this.client().rm(path); }
  cfgGet(progress?: Progress) { return this.client().cfgGet(progress); }
  cfgPut(data: Uint8Array, progress?: Progress) { return this.client().cfgPut(data, progress); }
  mark() { return this.client().mark(); }
  setTime() { return this.client().setTime(Date.now()); }
  reboot() { return this.client().reboot(); }

  async pushAddressMap(mapText: string, progress?: Progress): Promise<string> {
    const hash = loads(mapText).versionHash;
    const data = buildMapFromToml(mapText);
    await this.client().put('/HANDHELD.MAP', data, progress);
    return `HANDHELD.MAP: map #${hash} sent (${data.length} bytes); the handheld uses it from its next power-up`;
  }

  mapFolderFiles(files: Record<string, Uint8Array>): Array<[string, Uint8Array]> {
    return mapFolderFiles(files);
  }

  putMapFiles(files: Array<[string, Uint8Array]>,
    progress?: (name: string, held: number, size: number, done: number, total: number) => void): Promise<unknown> {
    return this.client().putMapFiles(files, progress);
  }

  async flash(image: Uint8Array, baud: number, progress: (p: FlashProgress) => void): Promise<void> {
    const id = this.portId;
    const device = id ? this.opts.serialPortOf?.(id) ?? null : null;
    if (!device) throw new Error('Installing firmware needs Web Serial (Chrome or Edge on a desktop); over WebUSB it is not supported.');
    const hl = this.hl;
    const port = this.port;
    this.hl = null;
    this.port = null;
    await hl?.close().catch(() => undefined);
    await port?.close().catch(() => undefined);
    this.emitState('flashing', 'esptool');
    try {
      await flash(image, esptoolLoaderFactory(device), { baud, onProgress: progress });
    } finally {
      this.emitState('disconnected', 'firmware written: connect again');
      await this.opts.afterDisconnect?.();
    }
  }

  cancel(): void {
    if (this.hl) this.hl.cancelFlag = true;
  }
}
