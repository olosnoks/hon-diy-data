/**
 * Where files live: an abstract store of whole files by name. This is the
 * one FileStore interface of the app: src/platform/files.ts implements it
 * for OPFS (browser, installed PWA, Android TWA) and for memory only;
 * ``MemoryFileStore`` here is for tests and for a session that must not
 * keep anything. Names may contain "/" for one level of folders
 * ("maps/cb400.toml").
 *
 * ``write`` must replace the file atomically (write a temporary file, then
 * rename): the database is written whole, and a power cut in the middle of a
 * write must leave the previous version.
 */

export interface FileStore {
  /** The file's bytes, or null if there is no such file. */
  read(name: string): Promise<Uint8Array | null>;
  /** Create or replace the file. */
  write(name: string, data: Uint8Array): Promise<void>;
  remove(name: string): Promise<void>;
  /** Every file name, with folder prefixes, sorted. */
  list(): Promise<string[]>;
  /** Where the files live, for the Setup screen ("Browser storage (OPFS)"). */
  readonly description?: string;
  /** False when files vanish with the tab (no OPFS). */
  readonly persistent?: boolean;
}

export class MemoryFileStore implements FileStore {
  readonly description = 'Memory only (files are lost on reload)';
  readonly persistent = false;
  readonly files = new Map<string, Uint8Array>();
  writes = 0;

  async read(name: string): Promise<Uint8Array | null> {
    const f = this.files.get(name);
    return f ? f.slice() : null;
  }

  async write(name: string, data: Uint8Array): Promise<void> {
    this.writes++;
    this.files.set(name, data.slice());
  }

  async remove(name: string): Promise<void> {
    this.files.delete(name);
  }

  async list(): Promise<string[]> {
    return [...this.files.keys()].sort();
  }
}
