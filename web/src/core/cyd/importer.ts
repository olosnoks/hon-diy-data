/**
 * Import handheld drive files, one drive each (hondalog/handheld/importer.py).
 *
 * A drive comes out like a laptop recording: one ``ecu/AA+N`` stream per block with the
 * raw bytes and the values decoded by the map the handheld ran (found by its hash),
 * ``ecu/link``, a ``gps`` stream, markers, alarms, stall captures, and the map's own
 * ``address_map_hash``. Importing a card twice adds nothing: a drive is remembered by
 * the identity its file carries (``meta.handheld_id``).
 *
 * The database is agent #4's (src/core/store/): it implements ``ImportStore`` / ``ImportDrive``,
 * which mirror the Python Store/Drive calls the importer makes. Decoding a channel's raw
 * bytes needs the expression engine (agent #3) and an OBD2 reply the OBD2 decoder (agent
 * #6): both come in as callbacks.
 */

import * as F from './format';
import { type DriveFile, driveId, ecuIdText, epochUnix, isComplete, parseDriveFile, seconds, tLastUs } from './drivefile';
import { type HhChannel, type HhMap, isVerified } from './hhmap';
import { MAP_NAME, mapOfFile, readMap } from './mapfile';

export const LINK_STREAM = 'ecu/link';
export const GPS_STREAM = 'gps';
export const GPS_COLUMNS = ['gps_lat', 'gps_lon', 'gps_speed', 'gps_heading', 'gps_sats', 'gps_fix', 'gps_hdop', 'gps_utc'];

export interface StreamColumn { name: string; units: string; kind: 'value' | 'flag'; decimals: number; label?: string }

export interface ImportDrive {
  readonly name: string;
  addStream(name: string, columns: StreamColumn[], opts: { source: string; rawLen?: number; attrs?: Record<string, unknown>; storage?: string }): void;
  append(name: string, t: number, values: Array<number | null>, raw?: Uint8Array, latencyS?: number): void;
  event(t: number, kind: string, text: string, data?: Record<string, unknown>): void;
  capture(kind: string, t: number, start: number, end: number, opts: Record<string, unknown>): void;
  end(status: string, reason: string): void;
}

export interface ImportStore {
  /** Name of the drive this recording was imported as, or null. */
  alreadyImported(handheldId: string): Promise<string | null>;
  deleteDrive(name: string): Promise<void>;
  beginDrive(meta: Record<string, unknown>, startedUnix: number): Promise<ImportDrive>;
}

export interface ImportOptions {
  /** The map to decode with (default: found by the file's hash among ``maps``). */
  map?: HhMap | null;
  /** Maps to look in, with where they came from: the card's HANDHELD.MAP is tried first if given. */
  maps?: Array<{ map: HhMap; where: string }>;
  /** The card's HANDHELD.MAP bytes (it carries the map text). */
  cardMap?: Uint8Array | null;
  force?: boolean;
  allowOtherMap?: boolean;
  /** Raw channel bytes -> value (the expression engine); default: no value, the raw bytes are kept. */
  decode?: (ch: HhChannel, raw: Uint8Array) => number | null;
  /** An OBD2 Mode 01 reply -> [stream name, column, value] (the OBD2 decoder); default: raw only. */
  decodePid?: (pid: number, data: Uint8Array) => { stream: string; column: StreamColumn; value: number | null } | null;
  /** File modification time (unix s), the start time when the file has no clock. */
  mtimeUnix?: number;
  version?: string;
}

export interface ImportResult {
  name: string;
  status: 'imported' | 'skipped' | 'failed';
  reason: string;
  drive: string | null;
  reads: number;
  gps: number;
  captures: number;
  notes: string[];
}

export function resultText(r: ImportResult): string {
  if (r.status === 'imported') {
    return `${r.name}: imported as ${r.drive ?? '?'} (${r.reads} reads, ${r.gps} GPS fixes, ${r.captures} captures` +
      r.notes.map((n) => `; ${n}`).join('') + ')';
  }
  return `${r.name}: ${r.status}: ${r.reason}`;
}

const result = (name: string, status: ImportResult['status'], reason = ''): ImportResult =>
  ({ name, status, reason, drive: null, reads: 0, gps: 0, captures: 0, notes: [] });

/** The address map a file was recorded with, by hash: the given maps, then the card's HANDHELD.MAP. */
export function findMap(df: DriveFile, opts: ImportOptions): { map: HhMap | null; where: string } {
  if (opts.cardMap) {
    try {
      const mf = readMap(opts.cardMap);
      if (mf.mapHash === df.mapHash && mf.source) {
        const map = mapOfFile(mf);
        if (map && map.versionHash === df.mapHash) return { map, where: MAP_NAME };
      }
    } catch {
      /* not a map */
    }
  }
  for (const { map, where } of opts.maps ?? []) if (map.versionHash === df.mapHash) return { map, where };
  return { map: null, where: '' };
}

function columnFor(c: HhChannel): StreamColumn {
  return { name: c.name, units: c.units, kind: c.kind, decimals: c.decimals, label: c.label };
}

function fmt4g(v: number): string {
  if (v === 0) return '0';
  const e = Math.floor(Math.log10(Math.abs(v)));
  if (e < -4 || e >= 4) {
    const [m = '', x = '0'] = v.toExponential(3).split('e');
    const n = parseInt(x, 10);
    return `${m.replace(/\.?0+$/, '')}e${n < 0 ? '-' : '+'}${String(Math.abs(n)).padStart(2, '0')}`;
  }
  return String(Number(v.toPrecision(4)));
}

/** Import one parsed drive (or capture) file. */
export async function importDrive(df: DriveFile, store: ImportStore, opts: ImportOptions = {}): Promise<ImportResult> {
  const name = df.name;
  if (!(df.reads.length || df.iso.length || df.events.length || df.gps.length)) return result(name, 'skipped', 'nothing recorded in it');
  const id = driveId(df);
  const old = await store.alreadyImported(id);
  if (old && !opts.force) return result(name, 'skipped', `already imported as ${old}`);

  let map = opts.map ?? null;
  let where = '';
  if (map === null || (map.versionHash !== df.mapHash && !opts.allowOtherMap)) {
    const given = map ? [{ map, where: 'given map' }] : [];
    const found = findMap(df, { ...opts, maps: [...given, ...(opts.maps ?? [])] });
    map = found.map;
    where = found.where;
    if (map === null) {
      return result(name, 'failed', `recorded with address map #${df.mapHash}, which is not on the card, ` +
        'in the database or your map (pass it with --map)');
    }
  }
  const res = result(name, 'imported');
  res.reads = df.reads.length + df.iso.length;
  res.gps = df.gps.length;
  const epoch = epochUnix(df);
  let started: number;
  if (epoch !== null) {
    started = epoch + Math.min(0, seconds(df, firstT(df)));
  } else {
    started = opts.mtimeUnix ?? Date.now() / 1000;
    res.notes.push("no GPS time in the file: start time taken from the file's date");
  }
  if (df.badBlocks) res.notes.push(`${df.badBlocks} damaged stretch(es) skipped`);
  if (df.missingBlocks) res.notes.push(`${df.missingBlocks} block(s) never reached the card`);
  if (!isComplete(df)) res.notes.push('no end marker (power was cut)');
  const captureFile = df.kind === F.FK_CAPTURE;
  const tag = `${map.version} #${map.versionHash}`;
  const meta: Record<string, unknown> = {
    format: `hondalog ${opts.version ?? 'web'} (handheld ${df.fw})`,
    timebase: 'seconds since the drive started (handheld clock)',
    source: 'handheld',
    ecu: map.ecu,
    ecu_id: ecuIdText(df),
    port: `handheld ${F.BACKENDS[df.backend] ?? '?'}` + (df.linkMode ? ' ISO 9141-2' : ''),
    address_map_version: map.version,
    address_map_tag: tag,
    address_map_hash: map.versionHash,
    ecu_variant: map.variant,
    address_map_file: where,
    address_map_text: map.text,
    unverified_channels: map.channels.filter((c) => !c.verified).map((c) => c.name).join(' ') || 'none',
    units: map.channels.map((c) => `${c.name}=${c.units || '-'}`).join(' '),
    handheld_id: id,
    handheld: {
      device: df.device, boot: df.bootNo, file: name, firmware: df.fw, kind: captureFile ? 'capture' : 'drive',
      complete: isComplete(df), end_reason: df.end ? df.end.reason : 'power_off', blocks: df.blocks,
      bad_blocks: df.badBlocks, missing_blocks: df.missingBlocks, map_hash: df.mapHash,
      clock: epoch !== null ? 'gps' : 'unknown', config: df.config,
    },
    link: { reads: df.reads.length + df.iso.length, blocks: df.blocks, bad_blocks: df.badBlocks, missing_blocks: df.missingBlocks },
  };
  if (epoch !== null) meta.epoch_unix = epoch;
  if (map.versionHash !== df.mapHash) res.notes.push(`decoded with map #${map.versionHash}, recorded with #${df.mapHash}`);

  try {
    if (old && opts.force) await store.deleteDrive(old);
    const drive = await store.beginDrive(meta, started);
    writeReads(drive, df, map, opts);
    writeIso(drive, df, opts);
    writeLink(drive, df);
    writeGps(drive, df);
    res.captures = writeEvents(drive, df, map, epoch, started);
    drive.end('imported', df.end ? df.end.reason : 'power_off');
    res.drive = drive.name;
  } catch (e) {
    return result(name, 'failed', String((e as Error)?.message ?? e));
  }
  return res;
}

/** Import the bytes of one file. */
export async function importFile(name: string, data: Uint8Array, store: ImportStore, opts: ImportOptions = {}): Promise<ImportResult> {
  let df: DriveFile;
  try {
    df = parseDriveFile(data, name);
  } catch (e) {
    return result(name, 'failed', (e as Error).message);
  }
  return importDrive(df, store, opts);
}

/**
 * Every drive on a card (files: path -> bytes, as ``DRIVES/D000001.HHD``), oldest first. A capture
 * file is imported only when the drive it was cut from is not there.
 */
export async function importCard(files: Map<string, Uint8Array>, store: ImportStore, opts: ImportOptions = {}): Promise<ImportResult[]> {
  const results: ImportResult[] = [];
  const up = (p: string) => p.replace(/\\/g, '/').toUpperCase();
  const cardMap = opts.cardMap ?? [...files.entries()].find(([p]) => up(p) === MAP_NAME)?.[1] ?? null;
  const o = { ...opts, cardMap };
  const keys = [...files.keys()].sort();
  let drives = keys.filter((p) => up(p).startsWith('DRIVES/') && up(p).endsWith('.HHD'));
  const caps = keys.filter((p) => up(p).startsWith('CAPTURES/') && up(p).endsWith('.HHD'));
  if (!drives.length && !caps.length) drives = keys.filter((p) => !p.includes('/') && up(p).endsWith('.HHD'));
  const have = new Set<string>();
  for (const path of drives) {
    const res = await importFile(path, files.get(path)!, store, o);
    results.push(res);
    try {
      const df = parseDriveFile(files.get(path)!, path);
      if (df.kind === F.FK_DRIVE && res.status !== 'failed') have.add(`${df.device}|${df.bootNo}|${df.fileNo}`);
    } catch {
      /* counted above */
    }
  }
  for (const path of caps) {
    let df: DriveFile;
    try {
      df = parseDriveFile(files.get(path)!, path);
    } catch (e) {
      results.push(result(path, 'failed', (e as Error).message));
      continue;
    }
    if (df.driveNo && have.has(`${df.device}|${df.bootNo}|${df.driveNo}`)) {
      results.push(result(path, 'skipped', `is a window of drive D${String(df.driveNo).padStart(6, '0')}`));
      continue;
    }
    results.push(await importDrive(df, store, o));
  }
  return results;
}

function firstT(df: DriveFile): number {
  const first = [df.reads, df.iso, df.events].filter((s) => s.length).map((s) => s[0]!.tUs);
  return first.length ? Math.min(...first) : df.t0Us;
}

function writeReads(drive: ImportDrive, df: DriveFile, map: HhMap, opts: ImportOptions): void {
  const channels = new Map<string, HhChannel[]>();
  for (const r of df.reads) {
    const key = `${r.addr}+${r.data.length}`;
    const name = `ecu/${r.addr.toString(16).toUpperCase().padStart(2, '0')}+${r.data.length}`;
    let chans = channels.get(key);
    if (!chans) {
      chans = map.channels.filter((c) => r.addr <= c.addr && c.addr + c.length <= r.addr + r.data.length);
      channels.set(key, chans);
      drive.addStream(name, chans.map(columnFor), { source: 'ecu', rawLen: r.data.length,
        attrs: { addr: r.addr, length: r.data.length, mode: 'read' } });
    }
    const values = chans.map((c) => (opts.decode ? opts.decode(c, r.data.subarray(c.addr - r.addr, c.addr - r.addr + c.length)) : null));
    drive.append(name, seconds(df, r.tUs), values, r.data, r.latencyUs / 1e6);
  }
}

function writeIso(drive: ImportDrive, df: DriveFile, opts: ImportOptions): void {
  const defined = new Set<string>();
  for (const r of df.iso) {
    if (r.mode !== 0x41) continue;
    const pv = opts.decodePid?.(r.pid, r.data) ?? null;
    const name = pv?.stream ?? `obd2/${r.pid.toString(16).toUpperCase().padStart(2, '0')}`;
    const column = pv?.column ?? { name: `pid_${r.pid.toString(16).padStart(2, '0')}`, units: '', kind: 'value' as const, decimals: 0 };
    if (!defined.has(name)) {
      defined.add(name);
      drive.addStream(name, [column], { source: 'obd2', rawLen: r.data.length, attrs: { pid: r.pid, mode: 'obd2' } });
    }
    try {
      drive.append(name, seconds(df, r.tUs), [pv?.value ?? null], r.data);
    } catch {
      /* a reply of another length than the first: keep the stream consistent */
    }
  }
}

function writeLink(drive: ImportDrive, df: DriveFile): void {
  const changes = df.events.filter((e) => e.code === F.EV_LINK_UP || e.code === F.EV_LINK_DOWN)
    .map((e) => [e.tUs, e.code === F.EV_LINK_UP ? 1 : 0] as const);
  if (!changes.length) return;
  drive.addStream(LINK_STREAM, [{ name: 'link', units: '', kind: 'flag', decimals: 0 }], { source: 'ecu' });
  for (const [tUs, up] of changes) drive.append(LINK_STREAM, seconds(df, tUs), [up]);
}

function writeGps(drive: ImportDrive, df: DriveFile): void {
  if (!df.gps.length) return;
  drive.addStream(GPS_STREAM, GPS_COLUMNS.map((n) => ({ name: n, units: '', kind: 'value' as const, decimals: 7 })),
    { source: 'gps', storage: 'rows' });
  for (const g of df.gps) {
    const has = Boolean(g.fix);
    drive.append(GPS_STREAM, seconds(df, g.tUs), [has ? g.lat : null, has ? g.lon : null, has ? g.speedKmh : null,
      has ? g.heading : null, g.sats, has ? 1 : 0, g.hdop, g.utc ? g.utc : null]);
  }
}

/** Markers, alarms, link and trouble-code events; stall captures. Returns the number of captures. */
function writeEvents(drive: ImportDrive, df: DriveFile, map: HhMap, epoch: number | null, started: number): number {
  const rules = new Map(map.alarms.map((r) => [r.name, r]));
  const labels = new Map(map.channels.map((c) => [c.name, [c.label, c.units] as const]));
  const kinds: Record<number, string> = { [F.EV_ALARM_RAISED]: 'raised', [F.EV_ALARM_CLEARED]: 'cleared', [F.EV_ALARM_ACK]: 'acknowledged' };
  const words: Record<string, string> = { raised: 'ALARM', cleared: 'CLEAR', acknowledged: 'ACK' };
  for (const e of df.events) {
    const t = seconds(df, e.tUs);
    if (e.code === F.EV_MARKER) drive.event(t, 'marker', `MARK ${e.value}`);
    else if (e.code in kinds) {
      const name = df.alarmNames.get(e.src) ?? `alarm_${e.src}`;
      const rule = rules.get(name);
      const channel = rule ? rule.channel : '';
      const value = Number.isNaN(e.value) ? null : e.value;
      const [label, units] = labels.get(channel) ?? [channel, ''];
      const shown = channel && value !== null ? `${label} ${fmt4g(value)} ${units}`.trim() : (rule ? rule.label : name);
      const kind = kinds[e.code]!;
      drive.event(t, 'alarm', `${words[kind]} ${name}: ${shown}`, {
        alarm: name, kind, channel, value, severity: e.aux >= 2 ? 'critical' : 'warn',
        unverified: Boolean(channel) && !isVerified(map, channel),
      });
    } else if (e.code === F.EV_STALL) {
      const reason = F.STALL_REASONS[e.src] ?? String(e.src);
      drive.event(t, 'trigger', e.src === 1 ? 'STALL (rpm 0)' : e.src === 2 ? 'STALL (link lost)' : 'CAPTURE (alarm)', {
        rule: e.src === 1 ? 'stall' : e.src === 2 ? 'link_loss' : 'alarm', reason,
        message: F.STALL_MESSAGES[e.src] ?? reason, severity: 'critical',
      });
    } else if (e.code === F.EV_DTC) {
      const code = F.dtcText(Math.trunc(e.value));
      drive.event(t, 'dtc', code, { code, status: 'stored' });
    } else if (e.code === F.EV_DTC_NONE) drive.event(t, 'dtc', 'no stored trouble codes', {});
    else if (e.code === F.EV_PORT_LOST || e.code === F.EV_PORT_BACK) {
      drive.event(t, 'link', e.code === F.EV_PORT_LOST ? 'K-line cable lost' : 'K-line cable back', {});
    } else if (e.code === F.EV_ISO_SESSION) {
      const h2 = (n: number) => n.toString(16).toUpperCase().padStart(2, '0');
      drive.event(t, 'link', `ISO 9141-2 session, key bytes ${h2(e.src)} ${h2(e.aux & 0xff)}`, {});
    } else if (e.code === F.EV_LOG_DROPPED) {
      drive.event(t, 'note', `${e.value} log blocks were dropped: the card was too slow or missing`, {});
    }
  }

  const captures: Array<[number, number, number, number, boolean]> = df.captures.map((c) =>
    [c.tTriggerUs, c.tStartUs, c.tEndUs, c.reason, c.complete]);
  const done = new Set(captures.map((c) => c[0]));
  const last = tLastUs(df);
  for (const e of df.events) { // a stall whose window never closed: power went first
    if (e.code === F.EV_STALL && !captures.some(([t, s, tEnd]) => Math.abs(e.tUs - t) < 1000 || (s <= e.tUs && e.tUs <= tEnd))
      && !done.has(e.tUs)) {
      captures.push([e.tUs, Math.max(df.t0Us, e.tUs - 30_000_000), last, e.src, false]);
    }
  }
  if (df.kind === F.FK_CAPTURE && !captures.length) captures.push([df.triggerTUs, df.t0Us, last, df.reason, false]);
  captures.sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2] || a[3] - b[3] || Number(a[4]) - Number(b[4]));
  for (const [tTrigger, tStart, tEnd, reason, complete] of captures) {
    const tt = seconds(df, tTrigger);
    const wall = epoch !== null ? epoch + tt : started;
    const name = F.STALL_REASONS[reason] ?? String(reason);
    const message = F.STALL_MESSAGES[reason] ?? name;
    const rule = reason === 1 ? 'stall' : reason === 2 ? 'link_loss' : 'alarm';
    drive.capture(reason === 1 || reason === 2 ? 'stall' : 'capture', tt, Math.max(0, seconds(df, tStart)),
      seconds(df, Math.min(tEnd, Math.max(last, tTrigger))), {
        rule, reason: name, label: reason === 1 ? 'STALL (rpm 0)' : reason === 2 ? 'STALL (link lost)' : 'CAPTURE',
        message, severity: 'critical', wallUnix: wall, complete, fired: [{ rule, t: Math.round(tt * 1000) / 1000 }],
        meta: {
          trigger: rule, trigger_reason: name, trigger_message: message, trigger_time_s: tt.toFixed(3),
          window_complete: complete, source: 'handheld', stall_trigger: message, stall_time_s: tt.toFixed(3),
          stall_wall_time: new Date(wall * 1000).toISOString(),
        },
      });
  }
  return captures.length;
}
