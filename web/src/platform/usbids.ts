import type { PortInfo } from '../core/types';

/** FTDI FT232R: the KKL cable to the ECU. */
export const FTDI_FT232R = { vendorId: 0x0403, productId: 0x6001 } as const;
/** WCH CH340: the CYD handheld's USB-C. */
export const CH340 = { vendorId: 0x1a86, productId: 0x7523 } as const;

export const SERIAL_FILTERS: { vendorId: number; productId: number }[] = [
  { ...FTDI_FT232R },
  { ...CH340 },
];

export function hex4(n: number): string {
  return n.toString(16).padStart(4, '0');
}

export function guessRole(vendorId?: number, productId?: number): PortInfo['guess'] {
  if (vendorId === FTDI_FT232R.vendorId && productId === FTDI_FT232R.productId) return 'ecu';
  if (vendorId === CH340.vendorId && productId === CH340.productId) return 'cyd';
  return 'unknown';
}

export function chipName(vendorId?: number, productId?: number): string {
  if (vendorId === undefined) return 'serial port';
  if (vendorId === FTDI_FT232R.vendorId) return `FTDI ${hex4(vendorId)}:${hex4(productId ?? 0)}`;
  if (vendorId === CH340.vendorId) return `CH340 ${hex4(vendorId)}:${hex4(productId ?? 0)}`;
  return `USB ${hex4(vendorId)}:${hex4(productId ?? 0)}`;
}
