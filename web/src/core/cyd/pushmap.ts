/**
 * ``push-map`` and ``set-time`` as the app runs them (hondalog/handheld/cli.py ``push_map_to``,
 * ``_push_map``, ``_set_time``), over either session: the binary request/answer
 * (``CydClient``: PUT_BEGIN/DATA/END) or the HL text session (``HlClient``: HL PUT, with
 * resume). The handheld checks the map loads before it replaces the old one and uses it
 * from its next power-up; relaying starts once its HELLO carries the new map hash.
 */

import { HlClient } from './hl_client';
import type { HhMap } from './hhmap';
import { CydClient } from './link';
import { buildMap, newReport, type ExportReport } from './mapfile';
import * as P from './serial_proto';

export interface PushResult {
  mapHash: string;
  bytes: number;
  report: ExportReport;
  /** HANDHELD.CFG lines the handheld will skip (keys it does not know), when a config was sent. */
  cfgSkipped: number | null;
}

/** Send ``map`` as HANDHELD.MAP (and ``cfg`` as HANDHELD.CFG when given). */
export async function pushMap(target: CydClient | HlClient, map: HhMap, opts: {
  cfg?: Uint8Array; progress?: (done: number, total: number) => void;
} = {}): Promise<PushResult> {
  const report = newReport();
  const data = buildMap(map, map.text, null, report);
  let cfgSkipped: number | null = null;
  if (target instanceof CydClient) {
    await target.push(P.F_MAP, data, opts.progress);
    if (opts.cfg) cfgSkipped = await target.push(P.F_CFG, opts.cfg);
  } else {
    await target.put('/HANDHELD.MAP', data, opts.progress);
    if (opts.cfg) {
      const note = await target.cfgPut(opts.cfg);
      const m = /skipped=(\d+)/.exec(note);
      cfgSkipped = m ? parseInt(m[1]!, 10) : 0;
    }
  }
  return { mapHash: map.versionHash, bytes: data.length, report, cfgSkipped };
}

/** The message ``push-map`` prints. */
export function pushText(r: PushResult): string {
  let s = `HANDHELD.MAP: map #${r.mapHash} sent (${r.bytes} bytes); the handheld uses it from its next power-up`;
  if (r.cfgSkipped !== null) {
    s += '\nHANDHELD.CFG sent' + (r.cfgSkipped ? `; the handheld will skip ${r.cfgSkipped} line(s) it does not know` : '');
  }
  return s;
}

/** Set the handheld's clock from this computer's. True: taken; false: its GPS has already set it. */
export async function setTime(target: CydClient | HlClient, unixMs: number = Date.now()): Promise<boolean> {
  if (target instanceof CydClient) return target.setTime(unixMs / 1000);
  return (await target.setTime(unixMs)) !== 'ignored';
}

export function setTimeText(taken: boolean): string {
  return taken ? 'clock set' : "not set: the handheld's GPS has already set its clock";
}
