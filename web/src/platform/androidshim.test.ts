// The Android app's navigator.usb shim (android/assets/hondalog-android.js),
// driven by the real WebUSB picker, FTDI driver and transport, with a fake of
// the Java side (UsbBridge.java) in front of the P2P simulator: the same path
// the KKL cable takes on the radio, minus the hardware.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { Link } from '../core/link/link';
import { EcuModel, P2P_FRAMING } from '../core/sim/model';
import { SimTransport } from '../core/sim/transport';
import { WebUsbPicker } from './picker';
import { ftdiBaudDivisor, SIO_SET_BAUD_RATE, SIO_SET_LATENCY_TIMER } from './webusb/ftdi';

const SHIM = readFileSync(resolve(__dirname, '../../../android/assets/hondalog-android.js'), 'utf8');
const PACKET = 64;
const FTDI_DESC = {
  id: '/dev/bus/usb/001/004', vendorId: 0x0403, productId: 0x6001, deviceClass: 0, deviceSubclass: 0, deviceProtocol: 0,
  productName: 'FT232R USB UART', manufacturerName: 'FTDI', serialNumber: 'A50285BI',
  configurations: [{ configurationValue: 1, interfaces: [{
    interfaceNumber: 0, alternateSetting: 0, interfaceClass: 255, interfaceSubclass: 255, interfaceProtocol: 255,
    endpoints: [
      { endpointNumber: 1, direction: 'in', type: 'bulk', packetSize: PACKET },
      { endpointNumber: 2, direction: 'out', type: 'bulk', packetSize: PACKET },
    ],
  }] }],
};

const b64 = (b: Uint8Array) => Buffer.from(b).toString('base64');
const unb64 = (s: string | null) => new Uint8Array(Buffer.from(s ?? '', 'base64'));

/** UsbBridge.java in JS: async results through __hondalogUsbDone, FTDI framing on bulk IN. */
class FakeJava {
  sim = new SimTransport(new EcuModel({ revEveryS: 0 }));   // a stock P2P
  controls: { requestType: number; request: number; value: number; index: number }[] = [];
  open = false;
  claimed = new Set<number>();
  generation = 0;
  exports: { name: string; mime: string; bytes: number[]; done: boolean }[] = [];

  private done(id: number, ok: boolean, v: unknown) {
    const w = window as unknown as { __hondalogUsbDone: (id: number, ok: boolean, json: string | null) => void };
    setTimeout(() => w.__hondalogUsbDone(id, ok, v === null ? null : JSON.stringify(v)), 0);
  }
  private fail(id: number, name: string, message: string) { this.done(id, false, { name, message }); }

  usbGetDevices(id: number) { this.done(id, true, [FTDI_DESC]); }
  usbRequestDevice(id: number, filters: string) {
    const f = JSON.parse(filters) as { vendorId?: number }[];
    if (f.length && !f.some((x) => x.vendorId === 0x0403)) this.fail(id, 'NotFoundError', 'none');
    else this.done(id, true, FTDI_DESC);
  }
  usbOpen(id: number) { this.open = true; this.done(id, true, null); }
  usbClose(id: number) { this.open = false; this.generation++; this.done(id, true, null); }
  usbSelectConfiguration(id: number) { this.done(id, true, null); }
  usbClaimInterface(id: number, _dev: string, n: number) { this.claimed.add(n); this.done(id, true, null); }
  usbReleaseInterface(id: number, _dev: string, n: number) { this.claimed.delete(n); this.generation++; this.done(id, true, null); }
  usbControl(id: number, _dev: string, requestType: number, request: number, value: number, index: number, _data: string | null, length: number) {
    if (!this.open) return this.fail(id, 'InvalidStateError', 'not open');
    this.controls.push({ requestType, request, value, index });
    if (request === SIO_SET_BAUD_RATE && !(requestType & 0x80)) {
      const baud = [9600, 10400, 19200, 38400, 115200].find((b) => ftdiBaudDivisor(b).value === value && ftdiBaudDivisor(b).index === index);
      if (baud) void this.sim.setBaud(baud);
    }
    this.done(id, true, requestType & 0x80 ? { status: 'ok', data: b64(new Uint8Array(length)) } : { status: 'ok', bytesWritten: 0 });
  }
  usbTransferOut(id: number, _dev: string, _ep: number, data: string) {
    const bytes = unb64(data);
    void this.sim.write(bytes).then(() => this.done(id, true, { status: 'ok', bytesWritten: bytes.length }));
  }
  usbTransferIn(id: number, _dev: string, _ep: number, length: number) {
    const gen = this.generation;
    const loop = async () => {
      for (;;) {
        if (gen !== this.generation || !this.open) return this.fail(id, 'AbortError', 'The transfer was cancelled.');
        const got = await this.sim.read(Math.min(length, PACKET) - 2, 20).catch(() => new Uint8Array(0));
        if (got.length) {
          const pkt = new Uint8Array(got.length + 2);
          pkt.set([0x01, 0x60]);   // FTDI modem/line status
          pkt.set(got, 2);
          return this.done(id, true, { status: 'ok', data: b64(pkt) });
        }
        // status-only packets are swallowed on the Java side, like UsbBridge.transferIn
      }
    };
    void loop();
  }
  usbClearHalt(id: number) { this.done(id, true, null); }

  exportBegin(name: string, mime: string) { this.exports.push({ name, mime, bytes: [], done: false }); return String(this.exports.length - 1); }
  exportChunk(t: string, data: string) { this.exports[Number(t)]!.bytes.push(...unb64(data)); return ''; }
  exportCommit(t: string) { this.exports[Number(t)]!.done = true; return `Download/hondalog/${this.exports[Number(t)]!.name}`; }
}

let java: FakeJava;

beforeAll(async () => {
  java = new FakeJava();
  await java.sim.open({ baud: 10400 });
  (window as unknown as { HondalogAndroid: FakeJava }).HondalogAndroid = java;
  new Function(SHIM)();
});

afterAll(() => {
  delete (window as unknown as { HondalogAndroid?: unknown }).HondalogAndroid;
});

describe('Android navigator.usb shim', () => {
  it('lists the KKL cable through the page picker', async () => {
    expect(navigator.usb).toBeTruthy();
    const ports = await new WebUsbPicker().list();
    expect(ports).toHaveLength(1);
    expect(ports[0]!.vendorId).toBe(0x0403);
    const dev = (await navigator.usb.getDevices())[0]!;
    expect(dev.configurations[0]!.interfaces[0]!.alternate.endpoints.map((e) => `${e.direction}${e.endpointNumber}`)).toEqual(['in1', 'out2']);
    expect(await navigator.usb.getDevices()).toEqual([dev]);   // same object every time
  });

  it('wakes the P2P at 10400 and reads ECU RAM over the FTDI driver', async () => {
    const picker = new WebUsbPicker();
    const port = (await picker.list())[0]!;
    const t = picker.open(port);
    await t.open({ baud: 10400 });
    // vendor requests to the device: latency timer 1 ms and the 10400 divisor
    expect(java.controls.every((c) => c.requestType === 0x40)).toBe(true);
    expect(java.controls.some((c) => c.request === SIO_SET_LATENCY_TIMER && c.value === 1)).toBe(true);
    const d = ftdiBaudDivisor(10400);
    expect(java.controls.some((c) => c.request === SIO_SET_BAUD_RATE && c.value === d.value)).toBe(true);
    expect(java.sim.baud).toBe(10400);

    const link = new Link(t, P2P_FRAMING);
    await link.wake();
    expect(new TextDecoder().decode(await link.readMem(0x76, 3))).toBe('SIM');
    const rpm = await link.readMem(0x00, 2);
    expect(rpm.length).toBe(2);

    await t.close();   // releaseInterface ends the pending transferIn without an error
    expect(java.open).toBe(false);
    expect(java.claimed.size).toBe(0);
  });

  it('fires connect and disconnect with the device', async () => {
    const seen: string[] = [];
    const on = (e: Event) => seen.push(`${e.type}:${(e as Event & { device: USBDevice }).device.vendorId}`);
    navigator.usb.addEventListener('connect', on);
    navigator.usb.addEventListener('disconnect', on);
    const w = window as unknown as { __hondalogUsbEvent: (t: string, j: string) => void };
    w.__hondalogUsbEvent('disconnect', JSON.stringify(FTDI_DESC));
    w.__hondalogUsbEvent('connect', JSON.stringify(FTDI_DESC));
    expect(seen).toEqual(['disconnect:1027', 'connect:1027']);
  });

  it('turns a USB error into a DOMException with its name', async () => {
    const dev = (await navigator.usb.getDevices())[0]!;
    await expect(dev.controlTransferOut({ requestType: 'vendor', recipient: 'device', request: 0, value: 0, index: 0 }))
      .rejects.toMatchObject({ name: 'InvalidStateError' });
    await expect(navigator.usb.requestDevice({ filters: [{ vendorId: 0x1234 }] })).rejects.toMatchObject({ name: 'NotFoundError' });
  });

  it('saves <a download> clicks through the app', async () => {
    const a = document.createElement('a');
    a.href = 'data:text/csv;base64,' + Buffer.from('t,rpm\n0,800\n').toString('base64');
    a.download = 'drive.csv';
    a.click();
    await new Promise((r) => setTimeout(r, 200));
    const e = java.exports.at(-1)!;
    expect(e.name).toBe('drive.csv');
    expect(e.done).toBe(true);
    expect(new TextDecoder().decode(new Uint8Array(e.bytes))).toBe('t,rpm\n0,800\n');
  });
});
