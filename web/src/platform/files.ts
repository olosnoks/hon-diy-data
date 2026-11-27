/**
 * The platform's file storage for the app's own files (hondalog.db, maps,
 * exports), behind the one FileStore interface of src/core/store/filestore.ts
 * (SqliteDriveStore.open takes these directly). Names may contain "/" for
 * one level of folders ("maps/cb400.toml").
 */
import type { FileStore } from '../core/store/filestore';

export type { FileStore };

function splitName(name: string): string[] {
  const parts = name.split('/').filter((p) => p.length > 0);
  if (parts.length === 0 || parts.some((p) => p === '.' || p === '..')) throw new Error(`bad file name: ${name}`);
  return parts;
}

/** Origin Private File System (Chrome, Edge, Firefox, Safari 15.2+). */
export class OpfsFileStore implements FileStore {
  readonly description = 'Browser storage (OPFS)';
  readonly persistent = true;
  private rootP: Promise<FileSystemDirectoryHandle> | null = null;

  constructor(private readonly folder = 'hondalog') {}

  static available(): boolean {
    return typeof navigator !== 'undefined' && !!navigator.storage && typeof navigator.storage.getDirectory === 'function';
  }

  private root(): Promise<FileSystemDirectoryHandle> {
    if (!this.rootP) {
      this.rootP = (async () => {
        // Ask the browser not to evict the logs under storage pressure.
        try {
          await navigator.storage.persist?.();
        } catch {
          /* optional */
        }
        const top = await navigator.storage.getDirectory();
        return top.getDirectoryHandle(this.folder, { create: true });
      })();
    }
    return this.rootP;
  }

  private async dirFor(parts: string[], create: boolean): Promise<FileSystemDirectoryHandle | null> {
    let dir = await this.root();
    for (const p of parts.slice(0, -1)) {
      try {
        dir = await dir.getDirectoryHandle(p, { create });
      } catch (e) {
        if (!create && (e as DOMException).name === 'NotFoundError') return null;
        throw e;
      }
    }
    return dir;
  }

  async read(name: string): Promise<Uint8Array | null> {
    const parts = splitName(name);
    const dir = await this.dirFor(parts, false);
    if (!dir) return null;
    try {
      const fh = await dir.getFileHandle(parts[parts.length - 1]!);
      const f = await fh.getFile();
      return new Uint8Array(await f.arrayBuffer());
    } catch (e) {
      const n = (e as DOMException).name;
      if (n === 'NotFoundError' || n === 'TypeMismatchError') return null;
      throw e;
    }
  }

  async write(name: string, bytes: Uint8Array): Promise<void> {
    const parts = splitName(name);
    const dir = (await this.dirFor(parts, true))!;
    const fh = await dir.getFileHandle(parts[parts.length - 1]!, { create: true });
    // createWritable writes to a swap file and swaps on close: a crash mid-write
    // leaves the old file intact.
    const w = await fh.createWritable({ keepExistingData: false });
    try {
      await w.write(bytes as unknown as FileSystemWriteChunkType);
      await w.close();
    } catch (e) {
      try {
        await w.abort();
      } catch {
        /* nothing to abort */
      }
      throw e;
    }
  }

  async list(): Promise<string[]> {
    const out: string[] = [];
    const walk = async (dir: FileSystemDirectoryHandle, prefix: string) => {
      // entries() is not in every lib.dom version; go through values().
      for await (const h of (dir as unknown as { values(): AsyncIterable<FileSystemHandle> }).values()) {
        if (h.kind === 'file') out.push(prefix + h.name);
        else await walk(h as FileSystemDirectoryHandle, `${prefix}${h.name}/`);
      }
    };
    await walk(await this.root(), '');
    return out.sort();
  }

  async remove(name: string): Promise<void> {
    const parts = splitName(name);
    const dir = await this.dirFor(parts, false);
    if (!dir) return;
    try {
      await dir.removeEntry(parts[parts.length - 1]!);
    } catch (e) {
      if ((e as DOMException).name !== 'NotFoundError') throw e;
    }
  }
}

/** In-memory fallback (tests, browsers without OPFS). Files last as long as the page. */
export class MemoryFileStore implements FileStore {
  readonly description = 'Memory only (this browser has no OPFS; files are lost on reload)';
  readonly persistent = false;
  private files = new Map<string, Uint8Array>();

  async read(name: string): Promise<Uint8Array | null> {
    const f = this.files.get(splitName(name).join('/'));
    return f ? f.slice() : null;
  }

  async write(name: string, bytes: Uint8Array): Promise<void> {
    this.files.set(splitName(name).join('/'), bytes.slice());
  }

  async list(): Promise<string[]> {
    return [...this.files.keys()].sort();
  }

  async remove(name: string): Promise<void> {
    this.files.delete(splitName(name).join('/'));
  }
}
