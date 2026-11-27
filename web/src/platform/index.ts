/**
 * Platform detection: the only place that knows how a serial port is opened
 * and where files live. The UI calls getTransportPicker(), getFileStore(),
 * platformName() and capabilities().
 *
 * Serial paths:
 *   desktop Chrome/Edge          -> Web Serial (the OS driver owns the chip)
 *   Android Chrome / PWA / TWA   -> WebUSB with the JS FTDI/CH340 drivers
 *                                   (Web Serial on Android does not cover them)
 *   anything else                -> none (the simulator still works)
 */
import type { TransportPicker } from '../core/types';
import { MemoryFileStore, OpfsFileStore, type FileStore } from './files';
import { WebSerialPicker, WebUsbPicker } from './picker';
import { isStandalone, isTwa } from './pwa';

export type { FileStore } from './files';
export { registerServiceWorker, isStandalone, isTwa } from './pwa';
export { SERIAL_FILTERS, FTDI_FT232R, CH340 } from './usbids';

export type SerialPath = 'webserial' | 'webusb' | 'none';
export type PlatformName = 'web' | 'pwa' | 'android-web' | 'android-pwa' | 'android-app';

export interface Capabilities {
  platform: PlatformName;
  serial: SerialPath;
  /** Browser OPFS (persistent) or memory only. */
  files: 'opfs' | 'memory';
  offline: boolean;
  /** Short notes for the Setup screen and the connect dialog; empty when all is well. */
  notes: string[];
}

function hasWebSerial(): boolean {
  return typeof navigator !== 'undefined' && 'serial' in navigator && !!navigator.serial;
}

function hasWebUsb(): boolean {
  return typeof navigator !== 'undefined' && 'usb' in navigator && !!navigator.usb;
}

export function isAndroid(): boolean {
  if (typeof navigator === 'undefined') return false;
  const uaData = (navigator as unknown as { userAgentData?: { platform?: string } }).userAgentData;
  if (uaData?.platform) return uaData.platform === 'Android';
  return /Android/i.test(navigator.userAgent);
}

export function serialPath(): SerialPath {
  if (isAndroid()) return hasWebUsb() ? 'webusb' : hasWebSerial() ? 'webserial' : 'none';
  if (hasWebSerial()) return 'webserial';
  if (hasWebUsb()) return 'webusb';
  return 'none';
}

export function platformName(): PlatformName {
  if (isAndroid()) return isTwa() ? 'android-app' : isStandalone() ? 'android-pwa' : 'android-web';
  return isStandalone() ? 'pwa' : 'web';
}

let picker: TransportPicker | null | undefined;

/** The serial picker for this platform, or null when there is no serial access. */
export function getTransportPicker(): TransportPicker | null {
  if (picker === undefined) {
    const p = serialPath();
    picker = p === 'webserial' ? new WebSerialPicker() : p === 'webusb' ? new WebUsbPicker() : null;
  }
  return picker;
}

let store: FileStore | undefined;

export function getFileStore(): FileStore {
  if (!store) store = OpfsFileStore.available() ? new OpfsFileStore() : new MemoryFileStore();
  return store;
}

export function capabilities(): Capabilities {
  const serial = serialPath();
  const files = OpfsFileStore.available() ? 'opfs' : 'memory';
  const notes: string[] = [];
  if (serial === 'none') {
    notes.push(
      isAndroid()
        ? 'This browser has no WebUSB. Use Chrome on Android (or the hondalog app) to talk to the cable.'
        : 'Web Serial is not available in this browser. Use Chrome or Edge on a desktop to talk to the cable; the simulator works everywhere.',
    );
  } else if (serial === 'webusb') {
    notes.push('USB serial through WebUSB: FTDI FT232R (KKL cable) and CH340 (CYD) are supported. Plug in with an OTG adapter and allow access when asked.');
  }
  if (typeof window !== 'undefined' && !window.isSecureContext) {
    notes.push('Not a secure context (https or localhost): serial and offline mode are off.');
  }
  if (files === 'memory') notes.push('This browser has no private file storage (OPFS): drives are lost on reload. Export them before closing.');
  const offline = typeof navigator !== 'undefined' && 'serviceWorker' in navigator;
  return { platform: platformName(), serial, files, offline, notes };
}

/** For tests: forget the cached picker and store. */
export function _resetPlatform(): void {
  picker = undefined;
  store = undefined;
}
