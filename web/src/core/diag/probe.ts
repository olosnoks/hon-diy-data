/**
 * Read-only link probes (port of hondalog/diag/probe.py): how fast does the
 * ECU answer, how long a block does it accept, and which addresses answer at
 * all. Each takes a ``HondaLink`` and sends nothing but read requests.
 */

import { hex2 } from '../obd2/bytes';
import { ProtocolError, type HondaLink } from './honda';

export const BLOCK_LENGTHS = [1, 2, 4, 8, 16, 32, 48, 64];
export const GAP_CANDIDATES_S = [0.005, 0.002, 0.001]; // never tried below 1 ms

const median = (xs: number[]): number => {
  const s = [...xs].sort((a, b) => a - b);
  const n = s.length;
  return n % 2 ? s[(n - 1) / 2]! : (s[n / 2 - 1]! + s[n / 2]!) / 2;
};
const round = (x: number, nd: number): number => Number(x.toFixed(nd));

export interface TimingProbe {
  reads: number;
  failed: number;
  turnaroundMedianMs: number;
  turnaroundP95Ms: number;
  turnaroundMaxMs: number;
  overheadMs: number;
  usbLatencyMs: number;
  /** gap (s) -> failed reads, in the order tried */
  gapErrors: Map<number, number>;
  /** values for the address map's [protocol] table (snake_case TOML keys) */
  recommended: Record<string, number>;
  notes: string[];
}

export const timingOk = (p: TimingProbe): boolean => p.reads > 0 && p.failed <= Math.floor(p.reads / 20);

export function timingSummary(p: TimingProbe): string {
  if (!p.reads) return 'no read succeeded';
  return (
    `ECU turnaround median ${p.turnaroundMedianMs.toFixed(1)} ms, ` +
    `p95 ${p.turnaroundP95Ms.toFixed(1)} ms, max ${p.turnaroundMaxMs.toFixed(1)} ms ` +
    `over ${p.reads} reads (${p.failed} failed)`
  );
}

/**
 * Measure the ECU's turnaround and suggest timeouts. The reply timeout is
 * four times the slowest turnaround plus 20 ms, never under 50 ms. With
 * ``tuneGap`` it tries gaps of 5, 2 and 1 ms (30 reads each) and recommends
 * the shortest without an error. With ``apply`` the recommendations become
 * the link's limits right away.
 */
export async function probeTiming(
  link: HondaLink,
  opts: { reads?: number; addr?: number; length?: number; tuneGap?: boolean; apply?: boolean } = {},
): Promise<TimingProbe> {
  const { reads = 200, addr = 0, length = 2, tuneGap = true, apply = false } = opts;
  const out: TimingProbe = {
    reads: 0, failed: 0, turnaroundMedianMs: 0, turnaroundP95Ms: 0, turnaroundMaxMs: 0, overheadMs: 0,
    usbLatencyMs: 1000 * link.usbLatencyS, gapErrors: new Map(), recommended: {}, notes: [],
  };
  const turnarounds: number[] = [];
  const overheads: number[] = [];
  for (let i = 0; i < reads; i++) {
    try {
      const r = await link.readBlock(addr, length, 0);
      turnarounds.push(r.turnaroundS);
      overheads.push(r.tDone - r.tRequest - (5 + length + 3) * link.byteS);
    } catch (e) {
      if (!(e instanceof ProtocolError)) throw e;
      out.failed += 1;
    }
  }
  out.reads = turnarounds.length;
  if (!turnarounds.length) {
    out.notes.push('no read succeeded; nothing measured');
    return out;
  }
  turnarounds.sort((a, b) => a - b);
  const max = turnarounds[turnarounds.length - 1]!;
  out.turnaroundMedianMs = 1000 * median(turnarounds);
  out.turnaroundP95Ms = 1000 * turnarounds[Math.min(turnarounds.length - 1, Math.floor(0.95 * turnarounds.length))]!;
  out.turnaroundMaxMs = 1000 * max;
  out.overheadMs = 1000 * median(overheads);
  out.recommended['reply_timeout_s'] = round(Math.min(0.5, Math.max(0.05, 4 * max + 0.02)), 3);
  out.recommended['turnaround_s'] = round(median(overheads), 4);
  if (out.usbLatencyMs > 1.5) {
    out.notes.push(
      `USB latency timer is ${out.usbLatencyMs.toFixed(0)} ms, not 1 ms: every request is slower than it needs to be ` +
        '(set the latency timer to 1 ms in the driver)',
    );
  }
  if (tuneGap) {
    const saved = [link.gap, link.gapFloor] as const;
    try {
      for (const gap of GAP_CANDIDATES_S) {
        link.gap = link.gapFloor = gap;
        let errors = 0;
        for (let i = 0; i < 30; i++) {
          try {
            await link.readBlock(addr, length, 0);
          } catch (e) {
            if (!(e instanceof ProtocolError)) throw e;
            errors += 1;
          }
        }
        out.gapErrors.set(gap, errors);
      }
    } finally {
      [link.gap, link.gapFloor] = saved;
    }
    const clean = [...out.gapErrors].filter(([, e]) => e === 0).map(([g]) => g);
    if (clean.length) out.recommended['inter_request_delay_s'] = Math.min(...clean);
    else {
      out.recommended['inter_request_delay_s'] = Math.max(...GAP_CANDIDATES_S);
      out.notes.push('errors at every gap tried; the line is noisy, not too fast');
    }
  }
  if (apply && timingOk(out)) {
    link.replyTimeoutS = out.recommended['reply_timeout_s']!;
    const gap = out.recommended['inter_request_delay_s'];
    if (gap !== undefined) link.gap = link.gapFloor = gap;
  }
  return out;
}

export interface BlockLengthProbe {
  /** length -> answered, in the order tried */
  results: Map<number, boolean>;
  maxReadLen: number;
}

export function blockSummary(p: BlockLengthProbe): string {
  if (!p.maxReadLen) return 'no block length answered';
  const tried = [...p.results].map(([n, ok]) => `${n}${ok ? '' : ' (no)'}`).join(', ');
  return `largest block the ECU answered: ${p.maxReadLen} bytes (tried ${tried})`;
}

/** Find the longest read the ECU answers with a valid checksum. */
export async function probeBlockLength(link: HondaLink, addr = 0, lengths = BLOCK_LENGTHS): Promise<BlockLengthProbe> {
  const out: BlockLengthProbe = { results: new Map(), maxReadLen: 0 };
  for (const length of lengths) {
    if (addr + length > 0x100) break;
    let ok = false;
    for (let i = 0; i < 2; i++) {
      try {
        ok = (await link.readBlock(addr, length, 0)).data.length === length;
      } catch (e) {
        if (!(e instanceof ProtocolError)) throw e;
        continue;
      }
      if (ok) break;
    }
    out.results.set(length, ok);
    if (ok) out.maxReadLen = Math.max(out.maxReadLen, length);
  }
  return out;
}

export class Sweep {
  /** address -> byte */
  data = new Map<number, number>();
  /** [addr, length] runs that never answered */
  unanswered: [number, number][] = [];

  get answered(): number {
    return this.data.size;
  }

  /** Runs of printable bytes: where an ECU id or part number might be. */
  asciiRuns(minLen = 4): [number, string][] {
    const runs: [number, string][] = [];
    let start = 0;
    let text = '';
    for (let addr = 0; addr <= 0x100; addr++) {
      const b = this.data.get(addr) ?? -1;
      if (b >= 0x20 && b < 0x7f) {
        if (!text) start = addr;
        text += String.fromCharCode(b);
      } else {
        if (text.length >= minLen) runs.push([start, text]);
        text = '';
      }
    }
    return runs;
  }

  hexdump(): string {
    const head = '      ' + Array.from({ length: 16 }, (_, i) => hex2(i)).join(' ');
    const lines = [head];
    for (let row = 0; row < 0x100; row += 16) {
      const cells: string[] = [];
      let text = '';
      for (let a = row; a < row + 16; a++) {
        const b = this.data.get(a);
        cells.push(b === undefined ? '--' : hex2(b));
        const c = b ?? 0;
        text += c >= 0x20 && c < 0x7f ? String.fromCharCode(c) : '.';
      }
      lines.push(`${hex2(row)}:   ${cells.join(' ')}   ${text}`);
    }
    return lines.join('\n');
  }
}

/** Read every address once. Chunks that fail are retried byte by byte, so
 *  one dead address does not hide its neighbours. */
export async function sweep(link: HondaLink, start = 0, end = 0xff, chunk = 16): Promise<Sweep> {
  const out = new Sweep();
  for (let addr = start; addr <= end; addr += chunk) {
    const length = Math.min(chunk, end + 1 - addr);
    try {
      const data = (await link.readBlock(addr, length)).data;
      data.forEach((b, i) => out.data.set(addr + i, b));
    } catch (e) {
      if (!(e instanceof ProtocolError)) throw e;
      out.unanswered.push([addr, length]);
    }
  }
  if (out.data.size && out.unanswered.length) {
    const failed = out.unanswered;
    out.unanswered = [];
    for (const [addr, length] of failed) {
      for (let a = addr; a < addr + length; a++) {
        try {
          out.data.set(a, (await link.readBlock(a, 1, 0)).data[0]!);
        } catch (e) {
          if (!(e instanceof ProtocolError)) throw e;
          const last = out.unanswered[out.unanswered.length - 1];
          if (last && last[0] + last[1] === a) last[1] += 1;
          else out.unanswered.push([a, 1]);
        }
      }
    }
  }
  return out;
}
