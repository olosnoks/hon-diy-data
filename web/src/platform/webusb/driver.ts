/**
 * A USB-serial chip driver for WebUSB: the chip-specific control transfers
 * (baud, line format, break, modem lines) and how to unwrap bulk-IN packets.
 * The bulk endpoints and the read loop live in UsbSerialTransport.
 */
export interface UsbSerialDriver {
  readonly name: string;
  /** Interface number to claim. */
  readonly interfaceNumber: number;
  /** The receive latency the driver sets, in seconds (FTDI latency timer), when known. */
  readonly latencyS?: number;
  /** Called once after the interface is claimed. */
  init(dev: USBDevice, baud: number): Promise<void>;
  setBaud(dev: USBDevice, baud: number): Promise<void>;
  setBreak(dev: USBDevice, on: boolean): Promise<void>;
  /** Throw away anything held in the chip's RX FIFO. */
  purgeRx(dev: USBDevice): Promise<void>;
  /** Payload bytes of one bulk-IN transfer (FTDI prefixes every packet with 2 status bytes). */
  unwrap(data: Uint8Array, packetSize: number): Uint8Array;
}

export async function ctrlOut(dev: USBDevice, request: number, value: number, index: number, data?: BufferSource): Promise<void> {
  const r = await dev.controlTransferOut({ requestType: 'vendor', recipient: 'device', request, value, index }, data);
  if (r.status !== 'ok') throw new Error(`USB control 0x${request.toString(16)} failed: ${r.status}`);
}

export async function ctrlIn(dev: USBDevice, request: number, value: number, index: number, length: number): Promise<DataView> {
  const r = await dev.controlTransferIn({ requestType: 'vendor', recipient: 'device', request, value, index }, length);
  if (r.status !== 'ok' || !r.data) throw new Error(`USB control-in 0x${request.toString(16)} failed: ${r.status}`);
  return r.data;
}
