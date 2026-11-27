import { afterEach, describe, expect, it, vi } from 'vitest';
import { ByteQueue } from './bytequeue';
import { MemoryFileStore } from './files';
import { _resetPlatform, capabilities, getTransportPicker, serialPath } from './index';
import { guessRole } from './usbids';
import { WebSerialTransport } from './webserial';
import { ch340Divisor, Ch340Driver } from './webusb/ch340';
import { FtdiDriver, ftdiBaudDivisor, ftdiUnwrap, LINE_8N1, LINE_BREAK, SIO_SET_BAUD_RATE, SIO_SET_DATA, SIO_SET_LATENCY_TIMER } from './webusb/ftdi';
import { UsbSerialTransport } from './webusb/transport';

describe('ByteQueue', () => {
  it('returns buffered bytes up to max, across chunks', async () => {
    const q = new ByteQueue();
    q.push(Uint8Array.of(1, 2, 3));
    q.push(Uint8Array.of(4, 5));
    expect([...(await q.read(4, 10))]).toEqual([1, 2, 3, 4]);
    expect([...(await q.read(10, 10))]).toEqual([5]);
    expect(q.length).toBe(0);
  });

  it('resolves empty on timeout, never throws', async () => {
    const q = new ByteQueue();
    const t0 = Date.now();
    const r = await q.read(8, 30);
    expect(r.length).toBe(0);
    expect(Date.now() - t0).toBeGreaterThanOrEqual(25);
  });

  it('wakes a waiting read as soon as data arrives', async () => {
    const q = new ByteQueue();
    const p = q.read(8, 1000);
    setTimeout(() => q.push(Uint8Array.of(0x20)), 5);
    const t0 = Date.now();
    expect([...(await p)]).toEqual([0x20]);
    expect(Date.now() - t0).toBeLessThan(500);
  });

  it('a failed queue rejects empty reads but still hands out what is buffered', async () => {
    const q = new ByteQueue();
    q.push(Uint8Array.of(9));
    q.fail(new Error('unplugged'));
    expect([...(await q.read(8, 10))]).toEqual([9]);
    await expect(q.read(8, 10)).rejects.toThrow('unplugged');
  });

  it('clear discards everything', async () => {
    const q = new ByteQueue();
    q.push(Uint8Array.of(1, 2));
    q.clear();
    expect((await q.read(8, 0)).length).toBe(0);
  });
});

describe('FTDI FT232R', () => {
  it('encodes the BM/R divisor like ftdi_sio', () => {
    // 9600: 3 MHz / 312.5 -> 312 | frac(0.5)=1 << 14
    expect(ftdiBaudDivisor(9600)).toMatchObject({ value: 0x4138, index: 0 });
    expect(ftdiBaudDivisor(9600).actual).toBeCloseTo(9600, 6);
    // 10400: 3 MHz / 288.5
    expect(ftdiBaudDivisor(10400)).toMatchObject({ value: 0x4120, index: 0 });
    expect(Math.abs(ftdiBaudDivisor(10400).actual - 10400) / 10400).toBeLessThan(0.001);
    // 115200: 26.0 -> divisor3 = 208, frac 0
    expect(ftdiBaudDivisor(115200)).toMatchObject({ value: 26, index: 0 });
    // 5 baud (ISO 9141 init is bit-banged with break, but the divisor still must not overflow 17 bits)
    const slow = ftdiBaudDivisor(300);
    expect(slow.value | (slow.index << 16)).toBe((10000 >> 0) | 0);
    // special codes
    expect(ftdiBaudDivisor(3_000_000).value).toBe(0);
    expect(ftdiBaudDivisor(2_000_000).value).toBe(1);
  });

  it('strips the two status bytes from every 64-byte packet', () => {
    const pkt = (n: number, start: number) => Uint8Array.from([0x01, 0x60, ...Array.from({ length: n }, (_, i) => start + i)]);
    expect(ftdiUnwrap(Uint8Array.of(0x01, 0x60), 64).length).toBe(0);
    expect([...ftdiUnwrap(pkt(3, 10), 64)]).toEqual([10, 11, 12]);
    const two = new Uint8Array(64 + 5);
    two.set(pkt(62, 0), 0);
    two.set(pkt(3, 100), 64);
    const out = ftdiUnwrap(two, 64);
    expect(out.length).toBe(65);
    expect(out[61]).toBe(61);
    expect([...out.subarray(62)]).toEqual([100, 101, 102]);
  });
});

describe('CH340', () => {
  it('matches the vendor divisor table', () => {
    expect(ch340Divisor(9600)).toBe(0xb202);
    expect(ch340Divisor(115200)).toBe(0xcc03);
    expect(ch340Divisor(2400)).toBe(0xd901);
    expect(ch340Divisor(19200)).toBe(0xd902);
  });
});

// ------------------------------------------------------------- WebUSB transport

type Ctrl = { dir: 'out' | 'in'; request: number; value: number; index: number };

function mockUsbDevice(vendorId: number, productId: number, inEp: number, opts: { ch340Regs?: number } = {}) {
  const log: Ctrl[] = [];
  const written: Uint8Array[] = [];
  const pending: ((r: USBInTransferResult) => void)[] = [];
  const incoming: Uint8Array[] = [];
  let released = false;
  const deliver = () => {
    while (pending.length && incoming.length) {
      const d = incoming.shift()!;
      pending.shift()!({ status: 'ok', data: new DataView(d.buffer, d.byteOffset, d.byteLength) } as USBInTransferResult);
    }
  };
  const dev = {
    vendorId,
    productId,
    serialNumber: 'A1',
    opened: false,
    configuration: {
      interfaces: [
        {
          interfaceNumber: 0,
          alternate: {
            endpoints: [
              { endpointNumber: inEp, direction: 'in', type: 'bulk', packetSize: 64 },
              { endpointNumber: 2, direction: 'out', type: 'bulk', packetSize: 64 },
            ],
          },
          alternates: [],
        },
      ],
    },
    async open() {
      dev.opened = true;
    },
    async close() {
      dev.opened = false;
    },
    async selectConfiguration() {},
    async claimInterface() {
      released = false;
    },
    async releaseInterface() {
      released = true;
      while (pending.length) pending.shift()!(Promise.reject(new DOMException('aborted', 'AbortError')) as never);
    },
    async clearHalt() {},
    async controlTransferOut(setup: USBControlTransferParameters) {
      log.push({ dir: 'out', request: setup.request, value: setup.value, index: setup.index });
      return { status: 'ok', bytesWritten: 0 } as USBOutTransferResult;
    },
    async controlTransferIn(setup: USBControlTransferParameters, length: number) {
      log.push({ dir: 'in', request: setup.request, value: setup.value, index: setup.index });
      const b = new Uint8Array(length);
      if (setup.request === 0x5f) b[0] = 0x31;
      if (setup.request === 0x95) {
        b[0] = (opts.ch340Regs ?? 0xc3c1) & 0xff;
        b[1] = (opts.ch340Regs ?? 0xc3c1) >> 8;
      }
      return { status: 'ok', data: new DataView(b.buffer) } as USBInTransferResult;
    },
    transferIn(): Promise<USBInTransferResult> {
      if (released) return Promise.reject(new DOMException('released', 'InvalidStateError'));
      return new Promise((resolve, reject) => {
        pending.push((r) => (r instanceof Promise ? r.then(resolve, reject) : resolve(r)));
        deliver();
      });
    },
    async transferOut(_ep: number, data: BufferSource) {
      const u = new Uint8Array(data as ArrayBuffer);
      written.push(u.slice());
      return { status: 'ok', bytesWritten: u.length } as USBOutTransferResult;
    },
  };
  return {
    dev: dev as unknown as USBDevice,
    log,
    written,
    feed(bytes: Uint8Array) {
      incoming.push(bytes);
      deliver();
    },
  };
}

describe('UsbSerialTransport (FTDI)', () => {
  it('opens with 1 ms latency, 8N1, the baud divisor, then reads with status bytes stripped', async () => {
    const m = mockUsbDevice(0x0403, 0x6001, 1);
    const t = new UsbSerialTransport(m.dev);
    expect(t.usb).toEqual({ vendorId: 0x0403, productId: 0x6001 });
    await t.open({ baud: 10400 });
    expect(m.log).toContainEqual({ dir: 'out', request: SIO_SET_LATENCY_TIMER, value: 1, index: 0 });
    expect(m.log).toContainEqual({ dir: 'out', request: SIO_SET_DATA, value: LINE_8N1, index: 0 });
    expect(m.log).toContainEqual({ dir: 'out', request: SIO_SET_BAUD_RATE, value: 0x4120, index: 0 });

    m.feed(Uint8Array.of(0x01, 0x60)); // status only
    m.feed(Uint8Array.of(0x01, 0x60, 0x00, 0x05, 0x20));
    const r = await t.read(16, 200);
    expect([...r]).toEqual([0x00, 0x05, 0x20]);
    expect((await t.read(16, 20)).length).toBe(0);

    await t.write(Uint8Array.of(0x72, 0x05));
    expect([...m.written[0]!]).toEqual([0x72, 0x05]);

    await t.setBreak(true);
    expect(m.log.at(-1)).toEqual({ dir: 'out', request: SIO_SET_DATA, value: LINE_8N1 | LINE_BREAK, index: 0 });
    await t.setBreak(false);
    expect(m.log.at(-1)).toEqual({ dir: 'out', request: SIO_SET_DATA, value: LINE_8N1, index: 0 });

    await t.setBaud(9600);
    expect(m.log.at(-1)).toEqual({ dir: 'out', request: SIO_SET_BAUD_RATE, value: 0x4138, index: 0 });

    await t.close();
    await t.close(); // idempotent
    await expect(t.read(1, 1)).rejects.toThrow('not open');
  });

  it('drain drops buffered bytes', async () => {
    const m = mockUsbDevice(0x0403, 0x6001, 1);
    const t = new UsbSerialTransport(m.dev);
    await t.open({ baud: 9600 });
    m.feed(Uint8Array.of(0x01, 0x60, 1, 2, 3));
    await new Promise((r) => setTimeout(r, 5));
    await t.drain();
    expect((await t.read(8, 10)).length).toBe(0);
    await t.close();
  });
});

describe('UsbSerialTransport (CH340)', () => {
  it('inits, sets the divisor with bit 7, and toggles break via regs 0x05/0x18', async () => {
    const m = mockUsbDevice(0x1a86, 0x7523, 2);
    const t = new UsbSerialTransport(m.dev);
    await t.open({ baud: 115200 });
    expect(m.log).toContainEqual({ dir: 'out', request: 0xa1, value: 0, index: 0 });
    expect(m.log).toContainEqual({ dir: 'out', request: 0x9a, value: 0x1312, index: 0xcc83 });
    expect(m.log).toContainEqual({ dir: 'out', request: 0x9a, value: 0x2518, index: 0xc3 });

    await t.setBreak(true);
    // regs read back 0x05=0xc1, 0x18=0xc3 -> clear NBREAK (bit 0) and ENABLE_TX (bit 6)
    expect(m.log.at(-1)).toEqual({ dir: 'out', request: 0x9a, value: 0x1805, index: 0xc0 | (0x83 << 8) });

    m.feed(Uint8Array.of(0x55, 0xaa));
    expect([...(await t.read(8, 200))]).toEqual([0x55, 0xaa]);
    await t.close();
  });

  it('refuses a chip it has no driver for', () => {
    const m = mockUsbDevice(0x10c4, 0xea60, 1);
    expect(() => new UsbSerialTransport(m.dev)).toThrow('no WebUSB serial driver');
  });

  it('exposes driver objects', () => {
    expect(new FtdiDriver().name).toContain('FTDI');
    expect(new Ch340Driver().name).toBe('CH340');
  });
});

// ------------------------------------------------------------- Web Serial transport

function mockSerialPort() {
  let controller: ReadableStreamDefaultController<Uint8Array> | null = null;
  const signals: SerialOutputSignals[] = [];
  const opens: SerialOptions[] = [];
  const written: Uint8Array[] = [];
  const port = {
    readable: null as ReadableStream<Uint8Array> | null,
    writable: null as WritableStream<Uint8Array> | null,
    getInfo: () => ({ usbVendorId: 0x0403, usbProductId: 0x6001 }),
    async open(o: SerialOptions) {
      opens.push(o);
      port.readable = new ReadableStream<Uint8Array>({ start: (c) => void (controller = c) });
      port.writable = new WritableStream<Uint8Array>({ write: (c) => void written.push(c.slice()) });
    },
    async close() {
      port.readable = null;
      port.writable = null;
    },
    async setSignals(s: SerialOutputSignals) {
      signals.push(s);
    },
  };
  return { port: port as unknown as SerialPort, signals, opens, written, feed: (b: Uint8Array) => controller!.enqueue(b) };
}

describe('WebSerialTransport', () => {
  it('opens 8N1, reads through the buffer, writes, breaks, reopens on a baud change', async () => {
    const m = mockSerialPort();
    const t = new WebSerialTransport(m.port);
    expect(t.label).toBe('FTDI 0403:6001');
    await t.open({ baud: 10400 });
    expect(m.opens[0]).toMatchObject({ baudRate: 10400, dataBits: 8, parity: 'none', stopBits: 1 });
    m.feed(Uint8Array.of(1, 2, 3));
    expect([...(await t.read(2, 100))]).toEqual([1, 2]);
    expect([...(await t.read(8, 100))]).toEqual([3]);
    expect((await t.read(8, 15)).length).toBe(0);
    await t.write(Uint8Array.of(9));
    expect([...m.written[0]!]).toEqual([9]);
    await t.setBreak(true);
    expect(m.signals.at(-1)).toEqual({ break: true });
    await t.setBaud(9600);
    expect(m.opens.at(-1)).toMatchObject({ baudRate: 9600 });
    m.feed(Uint8Array.of(7));
    expect([...(await t.read(8, 100))]).toEqual([7]);
    await t.close();
    await t.close();
  });
});

// ------------------------------------------------------------- files + detection

describe('MemoryFileStore', () => {
  it('reads, writes, lists and removes, with folders', async () => {
    const s = new MemoryFileStore();
    expect(await s.read('x')).toBeNull();
    await s.write('maps/a.toml', Uint8Array.of(1));
    await s.write('hondalog.db', Uint8Array.of(2, 3));
    expect(await s.list()).toEqual(['hondalog.db', 'maps/a.toml']);
    expect([...(await s.read('maps/a.toml'))!]).toEqual([1]);
    await s.remove('maps/a.toml');
    await s.remove('nope');
    expect(await s.list()).toEqual(['hondalog.db']);
    await expect(s.write('../evil', Uint8Array.of(0))).rejects.toThrow('bad file name');
  });
});

describe('platform detection', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    _resetPlatform();
  });

  it('guesses roles from USB ids', () => {
    expect(guessRole(0x0403, 0x6001)).toBe('ecu');
    expect(guessRole(0x1a86, 0x7523)).toBe('cyd');
    expect(guessRole(0x1234, 1)).toBe('unknown');
  });

  it('no serial in jsdom: no picker and a note for the UI', () => {
    expect(serialPath()).toBe('none');
    expect(getTransportPicker()).toBeNull();
    expect(capabilities().notes.join(' ')).toMatch(/Web Serial is not available/);
  });

  it('desktop with Web Serial uses it; Android uses WebUSB', () => {
    vi.stubGlobal('navigator', { ...navigator, serial: {}, usb: {}, userAgent: 'Mozilla/5.0 (Windows NT 10.0)' });
    expect(serialPath()).toBe('webserial');
    vi.stubGlobal('navigator', { ...navigator, serial: {}, usb: {}, userAgent: 'Mozilla/5.0 (Linux; Android 14; Pixel 7)' });
    expect(serialPath()).toBe('webusb');
    vi.stubGlobal('navigator', { ...navigator, usb: {}, userAgent: 'Mozilla/5.0 (Linux; Android 14)' });
    expect(serialPath()).toBe('webusb');
  });
});
