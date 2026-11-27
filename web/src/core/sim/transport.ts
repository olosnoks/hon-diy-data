/**
 * The simulated ECU behind the Transport interface (port of
 * LoopbackTransport and make_transport in hondalog/transport.py).
 *
 * byteTiming=false: bytes come back at once (unit tests).
 * byteTiming=true: true timing at the line's baud. Every byte, echo included,
 * becomes readable 10 bit times after the one before it, the ECU's reply
 * starts `model.turnaroundS` after the last request byte, and the adapter
 * hands bytes over on `usbLatencyS` ticks like an FTDI latency timer.
 *
 * With a VirtualClock waiting costs no real time (tests, benchmarks). With
 * the real clock (the default) it is a live demo for the UI with no cable.
 * Break and baud changes reach the model, so the ISO 9141-2 5-baud init works.
 */

import type { Transport } from '../types';
import { REAL, type Clock } from '../link/clock';
import { LOW_USB_LATENCY_S, TransportError } from '../link/link';
import type { FullFraming } from '../link/protocol';
import { EcuModel, Faults, P2P_FRAMING, P2T_FRAMING, type EcuModelOptions } from './model';

export class SimTransport implements Transport {
  readonly kind = 'sim' as const;
  label: string;
  readonly model: EcuModel;
  readonly clock: Clock;
  readonly byteTiming: boolean;
  /** how long the adapter may sit on received bytes before handing them over */
  usbLatencyS: number;
  baud: number;
  private rx: [number, number][] = [];   // [readable at, byte], time order
  private wireFree = 0;                  // when our last TX byte ends
  private breakSince: number | null = null;
  private isOpen = true;                 // usable straight away; open() resets it

  constructor(model?: EcuModel | null, opts: {
    clock?: Clock; byteTiming?: boolean; usbLatencyS?: number; baud?: number; label?: string;
  } = {}) {
    this.clock = opts.clock ?? REAL;
    this.model = model ?? new EcuModel({ clock: this.clock.now.bind(this.clock) });
    this.byteTiming = opts.byteTiming ?? false;
    this.usbLatencyS = opts.usbLatencyS ?? (this.byteTiming ? LOW_USB_LATENCY_S : 0);
    this.baud = opts.baud ?? this.model.framing.baud;
    this.label = opts.label ?? 'sim:';
  }

  // -- helpers --

  private check(): void {
    if (this.model.unplugged(this.clock.now())) {
      this.isOpen = false;
      throw new TransportError('simulated cable unplugged');
    }
    if (!this.isOpen) throw new TransportError('port is not open');
  }

  /** When a byte that finished arriving at `t` becomes readable. */
  private deliver(t: number): number {
    const tick = this.usbLatencyS;
    if (tick <= 0) return t;
    return (Math.trunc(t / tick - 1e-9) + 1) * tick;
  }

  private queue(start: number, data: Uint8Array, byteS: number): number {
    let t = start;
    for (const b of data) {
      t += byteS;
      this.rx.push([this.byteTiming ? this.deliver(t) : 0, b]);
    }
    return t;
  }

  private sortRx(): void {
    this.rx.sort((a, b) => a[0] - b[0]);   // stable
  }

  // -- Transport --

  async open(opts?: { baud: number }): Promise<void> {
    if (this.model.unplugged(this.clock.now())) {
      throw new TransportError('simulated cable unplugged: no such device');
    }
    this.rx = [];
    this.wireFree = 0;
    this.breakSince = null;
    this.isOpen = true;
    this.baud = opts?.baud ?? this.model.framing.baud;
  }

  async close(): Promise<void> {
    this.isOpen = false;
  }

  async write(data: Uint8Array): Promise<void> {
    this.check();
    const now = this.clock.now();
    const byteS = this.byteTiming ? 10.0 / this.baud : 0;
    const start = Math.max(now, this.wireFree);
    const end = start + byteS * data.length;
    this.wireFree = end;
    const model = this.model;
    if (model.cableDead) return;
    if (model.echo) this.queue(start, model.echoOf(Uint8Array.from(data)), byteS);
    for (const reply of model.onBytes(Uint8Array.from(data), this.baud, end)) {
      const rb = this.byteTiming ? 10.0 / reply.baud : 0;
      let t = end + (this.byteTiming ? reply.delayS : 0);
      for (const c of reply.chunks) t = this.queue(t + (this.byteTiming ? c.gapS : 0), c.data, rb);
    }
    this.sortRx();
  }

  /** read_some: up to `max` bytes, as soon as any are readable. */
  async read(max: number, timeoutMs: number): Promise<Uint8Array> {
    return this.readSomeS(max, Math.max(0, timeoutMs) / 1000);
  }

  private async readSomeS(size: number, timeout: number): Promise<Uint8Array> {
    this.check();
    const clock = this.clock;
    let now = clock.now();
    const rx = this.rx;
    if (!rx.length || rx[0]![0] > now) {
      const wake = rx.length ? rx[0]![0] : Infinity;
      if (wake > now + timeout) {
        // Nothing in time. Without byte timing still give the caller's loop
        // something to wait on, as the Python loopback does.
        await clock.sleep(this.byteTiming ? timeout : Math.min(timeout, 0.02));
        return new Uint8Array(0);
      }
      await clock.sleep(wake - now);
      now = Math.max(clock.now(), wake);
    }
    let n = 0;
    while (n < this.rx.length && n < size && this.rx[n]![0] <= now) n++;
    const out = Uint8Array.from(this.rx.slice(0, n), (x) => x[1]);
    this.rx.splice(0, n);
    return out;
  }

  async drain(): Promise<void> {
    this.check();
    const now = this.clock.now();
    this.rx = this.rx.filter((x) => x[0] > now);
  }

  async setBaud(baud: number): Promise<void> {
    this.check();
    this.baud = baud;
  }

  async setBreak(on: boolean): Promise<void> {
    this.check();
    const now = this.clock.now();
    if (on && this.breakSince === null) {
      this.breakSince = now;
    } else if (!on && this.breakSince !== null) {
      const start = this.breakSince;
      this.breakSince = null;
      if (this.model.cableDead) return;
      for (const reply of this.model.onBreak(start, now)) {
        const rb = 10.0 / reply.baud;
        let t = now + reply.delayS;
        for (const c of reply.chunks) {
          t += c.gapS;
          for (const b of c.data) {
            t += rb;
            this.rx.push([this.deliver(t), b]);
          }
        }
      }
      this.sortRx();
    }
  }
}

export type SimPreset = 'p2p' | 'p2t' | 'iso' | 'flaky' | 'p2p-car';

export interface SimTransportOptions extends Omit<EcuModelOptions, 'clock' | 'framing'> {
  /** p2p (default): stock P2P, Honda at 10400 baud after the wake frame.
   *  p2t: kerpz's 9600 baud ECU. iso: answers OBD2 (ISO 9141-2) only.
   *  flaky: P2P on a poor cable. p2p-car: the P2P's link faults as seen on
   *  the car (Faults.p2p: damaged requests, a deaf ECU now and then). */
  preset?: SimPreset;
  /** Fault spec ('drop=0.01,...', 'flaky', 'p2p') or a Faults; overrides the preset's. */
  faultSpec?: string | Faults;
  clock?: Clock;
  /** default true: true byte timing */
  byteTiming?: boolean;
  usbLatencyS?: number;
  /** The baud the port opens at before open() is called (as a serial port
   *  would: the map's). Default: the simulated ECU's own speed. */
  baud?: number;
  framing?: FullFraming;
}

/** The built-in "sim:" ports (Python make_transport). */
export function createSimTransport(opts: SimTransportOptions = {}): SimTransport {
  const { preset = 'p2p', faultSpec, clock = REAL, byteTiming = true, usbLatencyS, baud, framing, ...model } = opts;
  const seed = model.seed ?? 1;
  let faults: Faults | null | undefined = model.faults;
  if (faultSpec !== undefined) {
    faults = typeof faultSpec === 'string' ? Faults.named(faultSpec, seed) : faultSpec;
  } else if (faults === undefined) {
    faults = preset === 'flaky' ? Faults.flaky(seed) : preset === 'p2p-car' ? Faults.p2p(seed) : null;
  }
  const ecu = new EcuModel({
    ...model,
    faults,
    framing: framing ?? (preset === 'p2t' ? P2T_FRAMING : P2P_FRAMING),
    hondaProtocol: model.hondaProtocol ?? preset !== 'iso',
    clock: clock.now.bind(clock),
  });
  const label = preset === 'p2p' ? 'sim:' : `sim:${preset}`;
  return new SimTransport(ecu, { clock, byteTiming, usbLatencyS, baud, label });
}
