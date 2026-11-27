/**
 * The handheld next to the laptop's own KKL cable (hondalog/handheld/companion.py).
 *
 * When the app reads the ECU through the laptop's cable and the handheld is on USB at
 * the same time, its serial link is opened as well, for what only the handheld has: its
 * GPS (fix, sats, PPS clock). Its fixes go to ``onFix`` on the ECU poller's time axis
 * (``t0``); the session records them in the ``gps`` stream as source ``handheld``. With
 * a fix they win over the laptop's own location.
 *
 * Port choice (``spec``): ``auto`` looks for a handheld (a CH340 1a86:7523, PortInfo
 * guess "cyd") and never takes the ECU's port; ``off`` opens nothing; anything else
 * names the port (``COM22``, ``/dev/ttyUSB0``, ``cyd:COM22@460800``).
 */

import type { PortInfo } from '../types';
import { HUNT_S, PREFIX } from './link';
import { CydLink, type CydLinkOptions } from './live';
import { CH340_ID, type CydPort } from './port';
import type { GpsRecord } from './serial_proto';

export const AUTO = 'auto';
export const OFF = 'off';
export const SOURCE = 'handheld';
/** A fix older than this no longer counts: the laptop's location takes over again. */
export const GPS_FRESH_S = 5.0;
/** How often "auto" looks for a handheld that is not there. */
export const RESCAN_S = 2.0;

/** A port name for comparison: no ``cyd:`` prefix or ``@baud``; COM names in capitals. */
export function normPort(name: string): string {
  let n = name.startsWith(PREFIX) ? name.slice(PREFIX.length) : name;
  n = (n.split('@')[0] ?? '').trim();
  if (!n) return '';
  if (n.toUpperCase().startsWith('COM') || n.startsWith('\\\\.\\')) return n.toUpperCase().replace('\\\\.\\', '');
  return n;
}

export function samePort(a: string, b: string): boolean {
  const na = normPort(a), nb = normPort(b);
  return Boolean(na) && na === nb;
}

/** ``COM22`` -> ``cyd:COM22``; ``cyd:...`` stays. */
export function specPort(spec: string): string {
  return spec.startsWith(PREFIX) ? spec : PREFIX + spec;
}

export function isHandheldPort(p: PortInfo): boolean {
  return p.guess === 'cyd' || (p.vendorId === CH340_ID.vendorId && p.productId === CH340_ID.productId);
}

/** How the companion finds and opens ports: the platform's TransportPicker, wrapped. */
export interface PortSource {
  list(): Promise<PortInfo[]>;
  open(port: PortInfo, baud: number | null): CydPort;
}

/** A handheld's port that is not ``exclude`` (the ECU's), or null. */
export async function findHandheld(ports: PortSource, exclude = ''): Promise<PortInfo | null> {
  for (const p of await ports.list()) {
    if (isHandheldPort(p) && !samePort(p.label, exclude) && !samePort(p.id, exclude)) return p;
  }
  return null;
}

export interface Fix extends GpsRecord {}

export interface CompanionOptions extends CydLinkOptions {
  spec?: string;
  exclude?: string;
  ports?: PortSource;
  onFix?: (t: number, fix: Fix) => void;
}

export class HandheldCompanion extends CydLink {
  readonly source = SOURCE;
  readonly spec: string;
  readonly exclude: string;
  readonly auto: boolean;
  readonly off: boolean;
  onFix?: (t: number, fix: Fix) => void;
  lastFix: Fix | null = null;
  lastFixAt = 0;
  sats = 0;
  fixes = 0;
  /** The port "auto" picked. */
  found = '';
  private nextScan = 0;
  private readonly refused: string;
  private readonly ports?: PortSource;

  /** ``port``: a port object to use as it is (tests, an already chosen port); otherwise ``spec`` decides. */
  constructor(port: CydPort | null, opts: CompanionOptions = {}) {
    super(port, opts);
    this.spec = (opts.spec ?? '').trim() || (port ? port.name : AUTO);
    this.exclude = opts.exclude ?? '';
    this.ports = opts.ports;
    this.onFix = opts.onFix;
    this.auto = port === null && this.spec.toLowerCase() === AUTO;
    this.off = port === null && this.spec.toLowerCase() === OFF;
    const named = port ? port.name : this.spec;
    this.refused = !this.auto && !this.off && samePort(named, this.exclude) ? `${named} is the ECU's port` : '';
  }

  // -- GPS --

  protected override gps(t: number, fix: GpsRecord): void {
    super.gps(t, fix);
    this.sats = Math.trunc(fix.gps_sats || 0);
    if (!fix.gps_fix) return;
    this.lastFix = { ...fix };
    this.lastFixAt = this.clock.now();
    this.fixes += 1;
    try {
      this.onFix?.(t, fix);
    } catch (e) {
      this.lastError = `onFix: ${String(e)}`;
    }
  }

  /** A fix from the handheld in the last GPS_FRESH_S seconds. */
  get gpsOk(): boolean {
    return this.lastFix !== null && this.clock.now() - this.lastFixAt < GPS_FRESH_S;
  }

  /** Opened a moment ago, no hello yet: its GPS may be about to arrive. */
  get starting(): boolean {
    return this.hello === null && this.state !== 'port_lost' && this.clock.now() - this.tOpen < HUNT_S + 1.0;
  }

  gpsText(): string {
    return this.gpsOk ? `GPS: handheld (${this.sats} sats)` : '';
  }

  /** One short line: which handheld, or why there is none. */
  statusText(): string {
    if (this.refused) return `handheld: ${this.refused}`;
    if (this.off) return '';
    const h = this.hello;
    if (h === null) {
      if (this.state === 'port_lost') return this.lastError ? `handheld: ${this.lastError.replace(/^port: /, '')}` : 'handheld: none';
      return 'handheld: connecting';
    }
    return `handheld ${h.device} fw ${h.fw}`;
  }

  // -- the port: "auto" looks again after every loss, never at the ECU's port --

  protected override async acquirePort(): Promise<CydPort | null> {
    if (this.refused || this.off) {
      this.lastError = this.refused || 'off';
      return null;
    }
    if (this.port !== null) return this.port;
    if (!this.ports) {
      this.lastError = 'no handheld found';
      return null;
    }
    const now = this.clock.now();
    if (now < this.nextScan) return null;
    this.nextScan = now + RESCAN_S;
    let info: PortInfo | null;
    if (this.auto) {
      info = await findHandheld(this.ports, this.exclude);
    } else {
      const want = specPort(this.spec);
      info = (await this.ports.list()).find((p) => samePort(p.label, want) || samePort(p.id, want)) ?? null;
    }
    if (info === null) {
      this.lastError = this.auto ? 'no handheld found' : `${this.spec}: not found`;
      return null;
    }
    this.found = info.label;
    const at = this.spec.indexOf('@');
    const baud = !this.auto && at >= 0 ? parseInt(this.spec.slice(at + 1), 10) || null : null;
    return this.ports.open(info, baud);
  }

  protected override portLost(e: Error): void {
    super.portLost(e);
    if (this.auto) this.port = null; // it may come back on another port
  }
}
