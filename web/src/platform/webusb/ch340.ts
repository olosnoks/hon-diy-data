/**
 * WCH CH340 / CH341 (1a86:7523) over WebUSB. Register layout, divisor search
 * and break handling follow the Linux ch341 driver.
 */
import { ctrlIn, ctrlOut, type UsbSerialDriver } from './driver';

export const CH341_REQ_READ_VERSION = 0x5f;
export const CH341_REQ_WRITE_REG = 0x9a;
export const CH341_REQ_READ_REG = 0x95;
export const CH341_REQ_SERIAL_INIT = 0xa1;
export const CH341_REQ_MODEM_CTRL = 0xa4;

export const CH341_REG_BREAK = 0x05;
export const CH341_REG_PRESCALER = 0x12;
export const CH341_REG_DIVISOR = 0x13;
export const CH341_REG_LCR = 0x18;
export const CH341_REG_LCR2 = 0x25;

export const CH341_NBREAK_BITS = 0x01;
export const CH341_LCR_ENABLE_RX = 0x80;
export const CH341_LCR_ENABLE_TX = 0x40;
export const CH341_LCR_CS8 = 0x03;

const CH341_BIT_DTR = 1 << 5;
const CH341_BIT_RTS = 1 << 6;

const CLKRATE = 48_000_000;
const clkDiv = (ps: number, fact: number) => 1 << (12 - 3 * ps - fact);
const minRate = (ps: number) => CLKRATE / (clkDiv(ps, 1) * 512);
const MIN_BPS = Math.ceil(CLKRATE / (clkDiv(0, 0) * 256));
const MAX_BPS = CLKRATE / (clkDiv(3, 0) * 2);

/** The 16-bit value written to registers 0x13:0x12 (divisor << 8 | fact << 2 | prescaler), without bit 7. */
export function ch340Divisor(baud: number): number {
  const speed = Math.min(Math.max(baud, MIN_BPS), MAX_BPS);
  let fact = 1;
  let ps = 3;
  for (; ps >= 0; ps--) if (speed > minRate(ps)) break;
  if (ps < 0) throw new Error(`CH340: ${baud} baud out of range`);
  let cd = clkDiv(ps, fact);
  let div = Math.floor(CLKRATE / (cd * speed));
  // Fall back to the lower base clock (fact = 0) when the divisor is too small or too large.
  if (div < 9 || div > 255) {
    div = Math.floor(div / 2);
    cd *= 2;
    fact = 0;
  }
  if (div < 2) throw new Error(`CH340: ${baud} baud out of range`);
  // Pick the next divisor if that rate is closer to the requested one.
  if (Math.floor((16 * CLKRATE) / (cd * div)) - 16 * speed >= 16 * speed - Math.floor((16 * CLKRATE) / (cd * (div + 1)))) div++;
  // Prefer the lower base clock when the divisor is even.
  if (fact === 1 && div % 2 === 0) {
    div /= 2;
    fact = 0;
  }
  return ((0x100 - div) << 8) | (fact << 2) | ps;
}

export class Ch340Driver implements UsbSerialDriver {
  readonly name = 'CH340';
  readonly interfaceNumber = 0;
  private version = 0;
  private readonly lcr = CH341_LCR_ENABLE_RX | CH341_LCR_ENABLE_TX | CH341_LCR_CS8;

  async init(dev: USBDevice, baud: number): Promise<void> {
    const v = await ctrlIn(dev, CH341_REQ_READ_VERSION, 0, 0, 2);
    this.version = v.getUint8(0);
    await ctrlOut(dev, CH341_REQ_SERIAL_INIT, 0, 0);
    await this.setBaud(dev, baud);
    // DTR + RTS on (the CYD's auto-reset circuit is driven by them through
    // esptool's sequence; idle state is both asserted like a terminal).
    await ctrlOut(dev, CH341_REQ_MODEM_CTRL, ~(CH341_BIT_DTR | CH341_BIT_RTS) & 0xff, 0);
  }

  async setBaud(dev: USBDevice, baud: number): Promise<void> {
    // Bit 7: hand over each byte at once instead of buffering 32 bytes.
    const val = ch340Divisor(baud) | 0x80;
    await ctrlOut(dev, CH341_REQ_WRITE_REG, (CH341_REG_DIVISOR << 8) | CH341_REG_PRESCALER, val);
    if (this.version >= 0x30 || this.version === 0) {
      await ctrlOut(dev, CH341_REQ_WRITE_REG, (CH341_REG_LCR2 << 8) | CH341_REG_LCR, this.lcr);
    }
  }

  async setBreak(dev: USBDevice, on: boolean): Promise<void> {
    const reg = (CH341_REG_LCR << 8) | CH341_REG_BREAK;
    const cur = await ctrlIn(dev, CH341_REQ_READ_REG, reg, 0, 2);
    let b0 = cur.getUint8(0);
    let b1 = cur.getUint8(1);
    if (on) {
      b0 &= ~CH341_NBREAK_BITS;
      b1 &= ~CH341_LCR_ENABLE_TX;
    } else {
      b0 |= CH341_NBREAK_BITS;
      b1 |= CH341_LCR_ENABLE_TX;
    }
    await ctrlOut(dev, CH341_REQ_WRITE_REG, reg, (b0 & 0xff) | ((b1 & 0xff) << 8));
  }

  async purgeRx(): Promise<void> {
    /* no purge request on the CH340: the transport discards its buffer */
  }

  unwrap(data: Uint8Array): Uint8Array {
    return data;
  }
}
