/**
 * Where files live: an abstract store of whole files by name. Platform agent
 * #12 implements it for OPFS (web) and the Tauri file system (Windows,
 * Linux, Android); ``MemoryFileStore`` is for tests and for a session that
 * must not keep anything.
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
  remove?(name: string): Promise<void>;
  list?(): Promise<string[]>;
}

export class MemoryFileStore implements FileStore {
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
