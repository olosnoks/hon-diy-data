/**
 * The address map in use, kept as a file in the platform FileStore
 * ("addressmap.toml"). First run: the shipped default map is written there.
 * Setup > Files can load the owner's own map (saveMap), F7 Bytes edits it.
 */
import { DEFAULT_MAP_PATH, DEFAULT_MAP_TEXT, loads, type AddressMap } from '../core/addressmap';
import type { FileStore } from '../core/store/filestore';

export const MAP_FILE = 'addressmap.toml';

export interface LoadedMap {
  text: string;
  map: AddressMap;
  /** Why the stored map was not used (it did not load), '' when it was. */
  error: string;
}

const enc = new TextEncoder();
const dec = new TextDecoder();

export class MapStore {
  constructor(readonly files: FileStore, readonly name = MAP_FILE) {}

  /** The stored map; the shipped one (written to storage on first run, or used when the stored one fails). */
  async load(): Promise<LoadedMap> {
    let bytes: Uint8Array | null = null;
    try {
      bytes = await this.files.read(this.name);
    } catch {
      bytes = null;
    }
    if (bytes === null) {
      await this.files.write(this.name, enc.encode(DEFAULT_MAP_TEXT)).catch(() => undefined);
      return { text: DEFAULT_MAP_TEXT, map: loads(DEFAULT_MAP_TEXT, null, DEFAULT_MAP_PATH), error: '' };
    }
    const text = dec.decode(bytes);
    try {
      return { text, map: loads(text, null, this.name), error: '' };
    } catch (e) {
      return { text: DEFAULT_MAP_TEXT, map: loads(DEFAULT_MAP_TEXT, null, DEFAULT_MAP_PATH), error: `${this.name} does not load (${(e as Error).message}); using the shipped map` };
    }
  }

  /** Check and store a new map. Throws (MapError) when it does not load; nothing is written then. */
  async save(text: string): Promise<AddressMap> {
    const map = loads(text, null, this.name);
    await this.files.write(this.name, enc.encode(text));
    return map;
  }
}
