/**
 * What is the USB serial adapter? (the browser-side subset of
 * hondalog/diag/ftdi.py). Web Serial and the Tauri plugin give the USB
 * vendor and product id only: no sysfs, no registry, so the latency timer
 * and the clone check (serial number, bcdDevice) cannot be read here. The
 * report says so and tells the user how to set the timer.
 */

import type { Transport } from '../types';

export const FTDI_VID = 0x0403;
export const FTDI_PIDS: Record<number, string> = {
  0x6001: 'FT232R', 0x6010: 'FT2232', 0x6011: 'FT4232H', 0x6014: 'FT232H', 0x6015: 'FT-X series',
};
const OTHER: Record<string, string> = {
  '1a86:7523': 'CH340', '1a86:5523': 'CH341', '10c4:ea60': 'CP210x', '067b:2303': 'PL2303',
};

export const WINDOWS_LATENCY_HINT =
  'Device Manager > Ports (COM & LPT) > the cable\'s COM port > ' +
  'Port Settings > Advanced > Latency Timer (msec) = 1, OK, then replug';
export const UDEV_LATENCY_RULE =
  'ACTION=="add", SUBSYSTEM=="usb-serial", DRIVER=="ftdi_sio", ATTR{latency_timer}="1"';
export const UDEV_RULE_PATH = '/etc/udev/rules.d/99-hondalog.rules';

/** The udev lines that name the cable and pin its latency timer to 1 ms. */
export function udevRule(): string {
  return (
    '# hondalog: KKL 409.1 K-line cable (FTDI FT232R)\n' +
    'SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", ' +
    'SYMLINK+="hondalog", GROUP="dialout", MODE="0660"\n' +
    '# FTDI receive latency 16 ms -> 1 ms, every time the cable is plugged in\n' +
    `${UDEV_LATENCY_RULE}\n`
  );
}

export function udevInstallHint(): string {
  return `sudo tee ${UDEV_RULE_PATH} >/dev/null <<'EOF'\n${udevRule()}EOF\nsudo udevadm control --reload-rules && sudo udevadm trigger`;
}

export type HostPlatform = 'windows' | 'linux' | 'android' | 'mac' | 'other';

export function detectPlatform(): HostPlatform {
  const ua = typeof navigator !== 'undefined' ? navigator.userAgent : '';
  if (/Android/i.test(ua)) return 'android';
  if (/Windows/i.test(ua)) return 'windows';
  if (/Mac OS X|Macintosh/i.test(ua)) return 'mac';
  if (/Linux|X11/i.test(ua)) return 'linux';
  return 'other';
}

export interface AdapterInfo {
  port: string;
  /** "usb-serial", "serial" (no USB identity), "simulator", "unknown" */
  kind: string;
  driver: string;
  vid: number | null;
  pid: number | null;
  chip: string;
  isFtdi: boolean;
  /** "unknown" here: genuine/clone needs descriptors the browser does not give */
  verdict: 'genuine-looking' | 'clone-suspected' | 'not-ftdi' | 'unknown';
  latencyMs: number | null;
  notes: string[];
}

export function usbId(a: AdapterInfo): string {
  return a.vid !== null && a.pid !== null
    ? `${a.vid.toString(16).padStart(4, '0')}:${a.pid.toString(16).padStart(4, '0')}`
    : '?';
}

export function detectAdapter(t: Transport): AdapterInfo {
  const info: AdapterInfo = {
    port: t.label, kind: 'unknown', driver: t.kind, vid: null, pid: null, chip: '',
    isFtdi: false, verdict: 'unknown', latencyMs: null, notes: [],
  };
  if (t.kind === 'sim') {
    info.kind = 'simulator';
    info.chip = 'built-in simulator';
    info.driver = 'sim';
    return info;
  }
  if (!t.usb) {
    info.kind = 'serial';
    info.notes.push('the port reported no USB identity (not a USB adapter, or the platform hides it)');
    return info;
  }
  info.kind = 'usb-serial';
  info.vid = t.usb.vendorId;
  info.pid = t.usb.productId;
  if (info.vid === FTDI_VID) {
    info.isFtdi = true;
    info.chip = FTDI_PIDS[info.pid] ?? 'FTDI';
    info.notes.push(
      'genuine or clone cannot be told from the browser (needs the serial number and bcdDevice); ' +
        'the latency timer cannot be read here either: set it to 1 ms in the driver',
    );
  } else {
    info.chip = OTHER[usbId(info)] ?? '';
    info.verdict = 'not-ftdi';
    info.notes.push(
      info.chip === 'CH340'
        ? 'a CH340 is the CYD handheld\'s chip, not a KKL cable: is this the right port?'
        : 'not an FTDI chip: KKL timing at 10400 baud and break control may not work',
    );
  }
  return info;
}
