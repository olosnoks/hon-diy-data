/**
 * FTDI FT232R (0403:6001) over WebUSB. Request numbers and the divisor
 * encoding follow the Linux ftdi_sio driver (ftdi_232bm_baud_base_to_divisor)
 * and libftdi.
 */
import { ctrlOut, type UsbSerialDriver } from './driver';

export const SIO_RESET = 0x00;
export const SIO_SET_MODEM_CTRL = 0x01;
export const SIO_SET_FLOW_CTRL = 0x02;
export const SIO_SET_BAUD_RATE = 0x03;
export const SIO_SET_DATA = 0x04;
export const SIO_SET_LATENCY_TIMER = 0x09;

const SIO_RESET_SIO = 0;
const SIO_RESET_PURGE_RX = 1;
const SIO_RESET_PURGE_TX = 2;

/** 8 data bits, no parity, 1 stop bit; bit 14 = break. */
export const LINE_8N1 = 0x0008;
export const LINE_BREAK = 1 << 14;

/** {value, index} for SIO_SET_BAUD_RATE on a BM/R-type chip (48 MHz base). */
export function ftdiBaudDivisor(baud: number): { value: number; index: number; actual: number } {
  const divfrac = [0, 3, 2, 4, 1, 5, 6, 7];
  const base = 48_000_000;
  const divisor3 = Math.round(base / (2 * baud)); // divisor * 8
  let divisor = (divisor3 >> 3) | (divfrac[divisor3 & 7]! << 14);
  if (divisor === 1) divisor = 0; // 3 Mbaud
  else if (divisor === 0x4001) divisor = 1; // 2 Mbaud
  // 3 MHz / (divisor3 / 8); the two special codes are 3 and 2 Mbaud.
  const actual = divisor === 0 ? 3_000_000 : divisor === 1 ? 2_000_000 : (base / 2) / divisor3;
  return { value: divisor & 0xffff, index: (divisor >>> 16) & 0xffff, actual };
}

/** Strip the 2 modem-status bytes that start every max-packet-size chunk of a bulk-IN transfer. */
export function ftdiUnwrap(data: Uint8Array, packetSize: number): Uint8Array {
  if (data.length <= 2) return new Uint8Array(0);
  const out = new Uint8Array(data.length);
  let n = 0;
  for (let off = 0; off < data.length; off += packetSize) {
    const end = Math.min(off + packetSize, data.length);
    if (end - off > 2) {
      out.set(data.subarray(off + 2, end), n);
      n += end - off - 2;
    }
  }
  return out.subarray(0, n);
}

export class FtdiDriver implements UsbSerialDriver {
  readonly name = 'FTDI FT232R';
  readonly interfaceNumber = 0;
  /** init() sets the latency timer to 1 ms. */
  readonly latencyS = 0.001;
  /** wIndex of the port: 0 on single-port chips (FT232R). */
  private readonly port = 0;
  private line = LINE_8N1;

  async init(dev: USBDevice, baud: number): Promise<void> {
    await ctrlOut(dev, SIO_RESET, SIO_RESET_SIO, this.port);
    await ctrlOut(dev, SIO_SET_FLOW_CTRL, 0, this.port); // no flow control
    this.line = LINE_8N1;
    await ctrlOut(dev, SIO_SET_DATA, this.line, this.port);
    // DTR and RTS on, as pyserial does on open (some KKL cables draw power from them).
    await ctrlOut(dev, SIO_SET_MODEM_CTRL, 0x0101, this.port);
    await ctrlOut(dev, SIO_SET_MODEM_CTRL, 0x0202, this.port);
    // 1 ms latency timer (default 16 ms): the K-line reply timing needs it, as
    // hondalog/transport.py lowers it on Linux.
    await ctrlOut(dev, SIO_SET_LATENCY_TIMER, 1, this.port);
    await this.setBaud(dev, baud);
    await ctrlOut(dev, SIO_RESET, SIO_RESET_PURGE_RX, this.port);
    await ctrlOut(dev, SIO_RESET, SIO_RESET_PURGE_TX, this.port);
  }

  async setBaud(dev: USBDevice, baud: number): Promise<void> {
    const d = ftdiBaudDivisor(baud);
    await ctrlOut(dev, SIO_SET_BAUD_RATE, d.value, d.index | this.port);
  }

  async setBreak(dev: USBDevice, on: boolean): Promise<void> {
    this.line = on ? this.line | LINE_BREAK : this.line & ~LINE_BREAK;
    await ctrlOut(dev, SIO_SET_DATA, this.line, this.port);
  }

  async purgeRx(dev: USBDevice): Promise<void> {
    await ctrlOut(dev, SIO_RESET, SIO_RESET_PURGE_RX, this.port);
  }

  unwrap(data: Uint8Array, packetSize: number): Uint8Array {
    return ftdiUnwrap(data, packetSize);
  }
}
