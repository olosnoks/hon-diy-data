/**
 * Port pickers for the browser. list() returns ports the user already granted
 * (no prompt); request() shows the browser's chooser (must run from a click).
 */
import type { PortInfo, Transport, TransportPicker } from '../core/types';
import { SERIAL_FILTERS, chipName, guessRole } from './usbids';
import { WebSerialTransport } from './webserial';
import { UsbSerialTransport, driverFor } from './webusb/transport';

type Filters = { vendorId: number; productId?: number }[];

export class WebSerialPicker implements TransportPicker {
  private ports = new Map<string, SerialPort>();
  private ids = new WeakMap<SerialPort, string>();
  private next = 1;

  private info(port: SerialPort): PortInfo {
    let id = this.ids.get(port);
    if (!id) {
      id = `serial:${this.next++}`;
      this.ids.set(port, id);
    }
    this.ports.set(id, port);
    const i = port.getInfo();
    return { id, label: chipName(i.usbVendorId, i.usbProductId), vendorId: i.usbVendorId, productId: i.usbProductId, guess: guessRole(i.usbVendorId, i.usbProductId) };
  }

  async list(): Promise<PortInfo[]> {
    const ports = await navigator.serial.getPorts();
    return ports.map((p) => this.info(p));
  }

  async request(filters: Filters = SERIAL_FILTERS): Promise<PortInfo | null> {
    try {
      // Filters are a preference, not a gate: an empty list lets the owner pick any adapter.
      const port = await navigator.serial.requestPort(filters.length ? { filters: filters.map((f) => ({ usbVendorId: f.vendorId, usbProductId: f.productId })) } : {});
      return this.info(port);
    } catch (e) {
      if ((e as DOMException).name === 'NotFoundError') return null; // chooser cancelled
      throw e;
    }
  }

  open(port: PortInfo): Transport {
    const p = this.ports.get(port.id);
    if (!p) throw new Error(`unknown port ${port.id}; list() or request() it first`);
    return new WebSerialTransport(p);
  }
}

export class WebUsbPicker implements TransportPicker {
  private devices = new Map<string, USBDevice>();
  private ids = new WeakMap<USBDevice, string>();
  private next = 1;

  private info(dev: USBDevice): PortInfo {
    let id = this.ids.get(dev);
    if (!id) {
      id = `usb:${this.next++}`;
      this.ids.set(dev, id);
    }
    this.devices.set(id, dev);
    const serial = dev.serialNumber ? ` ${dev.serialNumber}` : '';
    return { id, label: `${chipName(dev.vendorId, dev.productId)}${serial}`, vendorId: dev.vendorId, productId: dev.productId, guess: guessRole(dev.vendorId, dev.productId) };
  }

  async list(): Promise<PortInfo[]> {
    const devs = await navigator.usb.getDevices();
    return devs.filter((d) => driverFor(d.vendorId, d.productId)).map((d) => this.info(d));
  }

  async request(filters: Filters = SERIAL_FILTERS): Promise<PortInfo | null> {
    // WebUSB needs a driver, so only offer chips we have one for.
    const known = filters.filter((f) => f.productId === undefined || driverFor(f.vendorId, f.productId));
    try {
      const dev = await navigator.usb.requestDevice({ filters: (known.length ? known : SERIAL_FILTERS).map((f) => ({ vendorId: f.vendorId, productId: f.productId })) });
      return this.info(dev);
    } catch (e) {
      if ((e as DOMException).name === 'NotFoundError') return null;
      throw e;
    }
  }

  open(port: PortInfo): Transport {
    const d = this.devices.get(port.id);
    if (!d) throw new Error(`unknown device ${port.id}; list() or request() it first`);
    return new UsbSerialTransport(d);
  }
}
