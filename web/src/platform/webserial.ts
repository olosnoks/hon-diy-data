/**
 * Transport over the Web Serial API (Chrome / Edge desktop, recent Chrome on
 * Android). A background reader loop fills a ByteQueue; baud changes reopen
 * the port (Web Serial has no live baud change); break uses setSignals.
 */
import type { Transport } from '../core/types';
import { ByteQueue } from './bytequeue';
import { chipName } from './usbids';

export class WebSerialTransport implements Transport {
  readonly kind = 'webserial' as const;
  readonly label: string;
  readonly usb?: { vendorId: number; productId: number };

  private baud = 9600;
  private isOpen = false;
  private queue = new ByteQueue();
  private reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
  private loop: Promise<void> | null = null;
  private stopping = false;

  constructor(private readonly port: SerialPort) {
    const info = port.getInfo();
    if (info.usbVendorId !== undefined && info.usbProductId !== undefined) {
      this.usb = { vendorId: info.usbVendorId, productId: info.usbProductId };
    }
    this.label = chipName(info.usbVendorId, info.usbProductId);
  }

  async open(opts: { baud: number }): Promise<void> {
    if (this.isOpen) await this.close();
    this.baud = opts.baud;
    await this.port.open({
      baudRate: opts.baud,
      dataBits: 8,
      parity: 'none',
      stopBits: 1,
      flowControl: 'none',
      bufferSize: 4096,
    });
    this.isOpen = true;
    this.queue.reset();
    // KKL cables take power from DTR/RTS on some designs; keep both asserted.
    try {
      await this.port.setSignals({ dataTerminalReady: true, requestToSend: true, break: false });
    } catch {
      /* not every driver supports it */
    }
    this.startReader();
  }

  private startReader(): void {
    this.stopping = false;
    const readable = this.port.readable;
    if (!readable) throw new Error(`${this.label}: port has no readable stream`);
    const reader = readable.getReader();
    this.reader = reader;
    this.loop = (async () => {
      try {
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          if (value) this.queue.push(value);
        }
      } catch (e) {
        // BreakError / FramingError / ParityError are recoverable: the stream
        // is replaced, so restart on the new one. A lost device is not.
        const name = (e as DOMException)?.name;
        if (!this.stopping && (name === 'BreakError' || name === 'FramingError' || name === 'ParityError' || name === 'BufferOverrunError')) {
          try {
            reader.releaseLock();
          } catch {
            /* already released */
          }
          if (this.port.readable) {
            this.startReader();
            return;
          }
        }
        if (!this.stopping) this.queue.fail(new Error(`${this.label}: ${(e as Error)?.message ?? 'read failed'}`));
      } finally {
        try {
          reader.releaseLock();
        } catch {
          /* fine */
        }
      }
    })();
  }

  private async stopReader(): Promise<void> {
    this.stopping = true;
    const r = this.reader;
    this.reader = null;
    if (r) {
      try {
        await r.cancel();
      } catch {
        /* device gone */
      }
    }
    if (this.loop) {
      try {
        await this.loop;
      } catch {
        /* swallowed */
      }
      this.loop = null;
    }
  }

  async close(): Promise<void> {
    if (!this.isOpen) return;
    this.isOpen = false;
    await this.stopReader();
    try {
      await this.port.close();
    } catch {
      /* already closed / unplugged: close must not throw */
    }
    this.queue.clear();
  }

  async setBaud(baud: number): Promise<void> {
    if (baud === this.baud && this.isOpen) return;
    if (!this.isOpen) {
      this.baud = baud;
      return;
    }
    await this.close();
    await this.open({ baud });
  }

  async setBreak(on: boolean): Promise<void> {
    this.requireOpen();
    await this.port.setSignals({ break: on });
  }

  async write(bytes: Uint8Array): Promise<void> {
    this.requireOpen();
    const w = this.port.writable;
    if (!w) throw new Error(`${this.label}: port is not writable`);
    const writer = w.getWriter();
    try {
      await writer.write(bytes);
    } finally {
      writer.releaseLock();
    }
  }

  async read(max: number, timeoutMs: number): Promise<Uint8Array> {
    this.requireOpen();
    return this.queue.read(max, timeoutMs);
  }

  async drain(): Promise<void> {
    this.queue.clear();
  }

  private requireOpen(): void {
    if (!this.isOpen) throw new Error(`${this.label}: port is not open`);
  }
}
