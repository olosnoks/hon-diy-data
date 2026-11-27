/**
 * Transport over WebUSB with a JS chip driver (FTDI FT232R or CH340): the
 * fallback where Web Serial is missing, chiefly Chrome on Android (browser,
 * installed PWA and the TWA APK). Android binds no kernel driver to these
 * chips, so Chrome can claim the interface directly.
 */
import type { Transport } from '../../core/types';
import { ByteQueue } from '../bytequeue';
import { CH340, FTDI_FT232R, chipName } from '../usbids';
import { Ch340Driver } from './ch340';
import type { UsbSerialDriver } from './driver';
import { FtdiDriver } from './ftdi';

export function driverFor(vendorId: number, productId: number): UsbSerialDriver | null {
  if (vendorId === FTDI_FT232R.vendorId && productId === FTDI_FT232R.productId) return new FtdiDriver();
  if (vendorId === CH340.vendorId && (productId === CH340.productId || productId === 0x5523)) return new Ch340Driver();
  return null;
}

export class UsbSerialTransport implements Transport {
  /** Same contract kind as Web Serial: it is the browser's serial path. */
  readonly kind = 'webserial' as const;
  readonly label: string;
  readonly usb: { vendorId: number; productId: number };

  private readonly driver: UsbSerialDriver;
  private queue = new ByteQueue();
  private isOpen = false;
  private stopping = false;
  private loop: Promise<void> | null = null;
  private epIn = 0;
  private epOut = 0;
  private packetSize = 64;

  constructor(private readonly dev: USBDevice, driver?: UsbSerialDriver) {
    this.usb = { vendorId: dev.vendorId, productId: dev.productId };
    const d = driver ?? driverFor(dev.vendorId, dev.productId);
    if (!d) throw new Error(`no WebUSB serial driver for ${chipName(dev.vendorId, dev.productId)}`);
    this.driver = d;
    this.label = `${chipName(dev.vendorId, dev.productId)} (USB)`;
  }

  async open(opts: { baud: number }): Promise<void> {
    if (this.isOpen) await this.close();
    const dev = this.dev;
    if (!dev.opened) await dev.open();
    if (dev.configuration === null) await dev.selectConfiguration(1);
    await dev.claimInterface(this.driver.interfaceNumber);
    this.findEndpoints();
    await this.driver.init(dev, opts.baud);
    this.queue.reset();
    this.isOpen = true;
    this.startReader();
  }

  private findEndpoints(): void {
    const iface = this.dev.configuration?.interfaces.find((i) => i.interfaceNumber === this.driver.interfaceNumber);
    const alt = iface?.alternate ?? iface?.alternates[0];
    const eps = alt?.endpoints ?? [];
    const bin = eps.find((e) => e.direction === 'in' && e.type === 'bulk');
    const bout = eps.find((e) => e.direction === 'out' && e.type === 'bulk');
    if (!bin || !bout) throw new Error(`${this.label}: bulk endpoints not found`);
    this.epIn = bin.endpointNumber;
    this.epOut = bout.endpointNumber;
    this.packetSize = bin.packetSize || 64;
  }

  private startReader(): void {
    this.stopping = false;
    this.loop = (async () => {
      while (!this.stopping) {
        let r: USBInTransferResult;
        try {
          r = await this.dev.transferIn(this.epIn, this.packetSize * 4);
        } catch (e) {
          if (!this.stopping) this.queue.fail(new Error(`${this.label}: ${(e as Error)?.message ?? 'USB read failed'}`));
          return;
        }
        if (r.status === 'stall') {
          try {
            await this.dev.clearHalt('in', this.epIn);
          } catch {
            /* retry anyway */
          }
          continue;
        }
        if (r.status === 'babble' || !r.data) continue;
        const raw = new Uint8Array(r.data.buffer, r.data.byteOffset, r.data.byteLength);
        const payload = this.driver.unwrap(raw, this.packetSize);
        if (payload.length) this.queue.push(payload.slice());
      }
    })();
  }

  async close(): Promise<void> {
    if (!this.isOpen) return;
    this.isOpen = false;
    this.stopping = true;
    // Releasing the interface aborts the pending transferIn.
    try {
      await this.dev.releaseInterface(this.driver.interfaceNumber);
    } catch {
      /* unplugged */
    }
    if (this.loop) {
      try {
        await this.loop;
      } catch {
        /* swallowed */
      }
      this.loop = null;
    }
    try {
      await this.dev.close();
    } catch {
      /* close must not throw */
    }
    this.queue.clear();
  }

  async setBaud(baud: number): Promise<void> {
    if (!this.isOpen) return;
    // The chip takes a new divisor live; no reopen needed.
    await this.driver.setBaud(this.dev, baud);
  }

  async setBreak(on: boolean): Promise<void> {
    this.requireOpen();
    await this.driver.setBreak(this.dev, on);
  }

  async write(bytes: Uint8Array): Promise<void> {
    this.requireOpen();
    const r = await this.dev.transferOut(this.epOut, bytes as unknown as BufferSource);
    if (r.status !== 'ok' || r.bytesWritten !== bytes.length) {
      throw new Error(`${this.label}: write ${r.status}, ${r.bytesWritten}/${bytes.length} bytes`);
    }
  }

  async read(max: number, timeoutMs: number): Promise<Uint8Array> {
    this.requireOpen();
    return this.queue.read(max, timeoutMs);
  }

  async drain(): Promise<void> {
    this.requireOpen();
    try {
      await this.driver.purgeRx(this.dev);
    } catch {
      /* best effort */
    }
    this.queue.clear();
  }

  private requireOpen(): void {
    if (!this.isOpen) throw new Error(`${this.label}: port is not open`);
  }
}
