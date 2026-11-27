/**
 * ``sync``: the handheld's SD card over its USB-C serial link, without taking the card
 * out (hondalog/handheld/sync.py; two-way firmware only).
 *
 * The files are copied into a mirror of the card (``DRIVES/``, ``CAPTURES/``,
 * ``HANDHELD.MAP``) kept in the platform's storage (``MirrorStore``, agent #12), and the
 * mirror goes through the importer like a card in a reader, so what is already in the
 * database is skipped. Resumable at every level: a file already whole in the mirror is
 * not asked for again, a cut-off one (``.part``) carries on, every chunk has its CRC32.
 */

import { CydBusy, CydError, type CydClient, type FileEntry, type PartSink, UsedLength, entryName } from './link';
import * as P from './serial_proto';

/** A folder of files (the mirror of one handheld's card). Paths use '/'. */
export interface MirrorStore {
  read(path: string): Promise<Uint8Array | null>;
  write(path: string, data: Uint8Array): Promise<void>;
  append(path: string, data: Uint8Array): Promise<void>;
  /** null if there is no such file. */
  size(path: string): Promise<number | null>;
  rename(from: string, to: string): Promise<void>;
  remove(path: string): Promise<void>;
  list(): Promise<string[]>;
}

export class MemoryMirror implements MirrorStore {
  files = new Map<string, Uint8Array>();
  async read(p: string) { return this.files.get(p) ?? null; }
  async write(p: string, d: Uint8Array) { this.files.set(p, d.slice()); }
  async append(p: string, d: Uint8Array) {
    const old = this.files.get(p) ?? new Uint8Array(0);
    const n = new Uint8Array(old.length + d.length);
    n.set(old);
    n.set(d, old.length);
    this.files.set(p, n);
  }
  async size(p: string) { return this.files.get(p)?.length ?? null; }
  async rename(a: string, b: string) {
    const d = this.files.get(a);
    if (d) { this.files.set(b, d); this.files.delete(a); }
  }
  async remove(p: string) { this.files.delete(p); }
  async list() { return [...this.files.keys()]; }
}

/** A ``.part`` file in the mirror that becomes ``dest`` when whole. */
export class MirrorPart implements PartSink {
  constructor(private readonly store: MirrorStore, readonly dest: string) {}
  get part(): string { return this.dest + '.part'; }
  async existing() { return (await this.store.read(this.part)) ?? new Uint8Array(0); }
  async reset() { await this.store.write(this.part, new Uint8Array(0)); }
  async append(d: Uint8Array) { await this.store.append(this.part, d); }
  async truncate(n: number) { await this.store.write(this.part, ((await this.store.read(this.part)) ?? new Uint8Array(0)).slice(0, n)); }
  async finish() { await this.store.rename(this.part, this.dest); }
}

export interface SyncReport {
  device: string;
  /** Copied now (whole, or the rest of a .part). */
  fetched: string[];
  /** Already in the mirror. */
  present: string[];
  /** Still being written: not copied. */
  open: string[];
  bytes: number;
}

/** A handheld's mirror folder name: its device id, made safe. */
export function mirrorName(device: string): string {
  return Array.from(device, (ch) => (/[A-Za-z0-9\-_.]/.test(ch) ? ch : '_')).join('') || 'handheld';
}

const MANIFEST = 'SYNC.json';

class Manifest {
  sizes: Record<string, number> = {};
  constructor(private readonly store: MirrorStore) {}

  async load(): Promise<this> {
    try {
      const raw = await this.store.read(MANIFEST);
      if (raw) this.sizes = JSON.parse(new TextDecoder().decode(raw));
    } catch {
      this.sizes = {};
    }
    return this;
  }

  async have(entry: FileEntry, dest: string): Promise<boolean> {
    const size = await this.store.size(dest);
    if (size === null) return false;
    return size === entry.size || this.sizes[entryName(entry)] === entry.size;
  }

  async done(entry: FileEntry): Promise<void> {
    this.sizes[entryName(entry)] = entry.size;
    const keys = Object.keys(this.sizes).sort();
    const text = JSON.stringify(Object.fromEntries(keys.map((k) => [k, this.sizes[k]])), null, 1);
    await this.store.write(MANIFEST, new TextEncoder().encode(text));
  }
}

/**
 * Copy the handheld's drive (and capture) files into ``store``. ``endDrive``: end the drive being
 * recorded first (a new one starts at once while the ECU answers), so it can be copied too.
 * Import afterwards with ``importCard`` over the mirror's files.
 */
export async function sync(cyd: CydClient, store: MirrorStore, opts: {
  captures?: boolean; endDrive?: boolean; progress?: (name: string, done: number, total: number) => void;
} = {}): Promise<SyncReport> {
  const hello = cyd.hello ?? await cyd.open();
  cyd.needTwoWay();
  if (!(hello.caps & P.CAP_FILES)) {
    throw new CydError('sync: ' + (!(hello.flags & P.HF_SD) ? 'no SD card in the handheld' : 'this firmware cannot send files'));
  }
  if (opts.endDrive) {
    await cyd.drive(false);
    await cyd.drive(true);
  }
  const report: SyncReport = { device: hello.device, fetched: [], present: [], open: [], bytes: 0 };
  const kinds = [P.F_DRIVE, ...(opts.captures ?? true ? [P.F_CAPTURE] : [])];
  const manifest = await new Manifest(store).load();
  // the map the drives were recorded with, so the importer finds it by its hash
  for (const entry of await cyd.listFiles(P.F_MAP)) await fetchOne(cyd, store, entry, report, opts.progress, null);
  for (const kind of kinds) {
    for (const entry of await cyd.listFiles(kind)) {
      if (entry.open) {
        report.open.push(entryName(entry));
        continue;
      }
      await fetchOne(cyd, store, entry, report, opts.progress, manifest);
    }
  }
  return report;
}

async function fetchOne(cyd: CydClient, store: MirrorStore, entry: FileEntry, report: SyncReport,
  progress: ((name: string, done: number, total: number) => void) | undefined, manifest: Manifest | null): Promise<void> {
  const dest = entryName(entry);
  if (entry.kind === P.F_MAP && (await store.size(dest)) === entry.size) {
    // the same map? Its header holds the hash, the text's sha256 and the CRC of the rest
    const [head] = await cyd.readChunk(entry.kind, entry.fileNo, 0, 64);
    const mine = (await store.read(dest))!.subarray(0, 64);
    if (mine.length === head.length && mine.every((b, i) => b === head[i])) {
      report.present.push(dest);
      return;
    }
  }
  if (manifest && await manifest.have(entry, dest)) {
    report.present.push(dest);
    return;
  }
  const part = new MirrorPart(store, dest);
  const had = (await store.size(part.part)) ?? 0;
  const cut = entry.kind === P.F_DRIVE || entry.kind === P.F_CAPTURE ? new UsedLength() : undefined;
  try {
    await cyd.fetch(entry, part, progress ? (d, t) => progress(dest, d, t) : undefined, cut);
  } catch (e) {
    if (e instanceof CydBusy) {
      report.open.push(dest);
      return;
    }
    throw e;
  }
  report.fetched.push(dest);
  report.bytes += Math.max(0, ((await store.size(dest)) ?? 0) - had);
  if (manifest) await manifest.done(entry);
}

/** The mirror's files for ``importCard`` (path -> bytes), without the bookkeeping. */
export async function mirrorFiles(store: MirrorStore): Promise<Map<string, Uint8Array>> {
  const out = new Map<string, Uint8Array>();
  for (const p of await store.list()) {
    if (p.endsWith('.part') || p === MANIFEST) continue;
    const d = await store.read(p);
    if (d) out.set(p, d);
  }
  return out;
}
