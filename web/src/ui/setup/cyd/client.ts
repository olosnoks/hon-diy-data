/**
 * What the CYD black box tab needs from agent #5's src/core/cyd (HlClient +
 * the worker of hondalog/ui/handheld_worker.py), as one thin interface. The
 * integrator adapts HlClient (ls/get/put/rm/cfgGet/cfgPut/stat/mark/setTime/
 * reboot/putMapFiles), the console line stream, buildMapFromToml + put for
 * the address map push, and flash.ts for the firmware install.
 */

import type { FlashProgress } from '../../../core/cyd/flash';

export type Progress = (done: number, total: number) => void;

export interface CydPortChoice { id: string; label: string; guess?: 'ecu' | 'cyd' | 'unknown' }

export type CydState = 'disconnected' | 'connecting' | 'connected' | 'flashing';

export interface CydClient {
  /** Ports to offer (a CH340 1a86:7523 first when there is one). */
  ports(): Promise<CydPortChoice[]>;
  /** Show the browser's port chooser (call from a click); resolves with the picked port, null if cancelled. */
  requestPort?(): Promise<CydPortChoice | null>;
  /** Open the port and the HL session; resolves with the HL HELLO fields (id, fw, variant, map). */
  connect(portId: string): Promise<Record<string, string>>;
  disconnect(): Promise<void>;
  /** State changes the client sees by itself (cable pulled, reopen after a flash). */
  onState?(fn: (state: CydState, detail: string) => void): () => void;
  /** Console lines: ESP_LOG text, "< HL ..." for lines nobody asked for. */
  onConsole(fn: (line: string) => void): () => void;
  sendLine(text: string): void;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  stat(): Promise<Record<string, any>>;
  /** [files [path, size, mtime][], dirs] */
  ls(path: string): Promise<[Array<[string, number, number]>, string[]]>;
  get(path: string, progress?: Progress): Promise<Uint8Array>;
  put(path: string, data: Uint8Array, progress?: Progress): Promise<string>;
  rm(path: string): Promise<void>;
  cfgGet(progress?: Progress): Promise<Uint8Array>;
  cfgPut(data: Uint8Array, progress?: Progress): Promise<string>;
  mark(): Promise<void>;
  /** 'ok', or another word when the handheld keeps GPS time. */
  setTime(): Promise<string>;
  reboot(): Promise<void>;
  /** Compile the address map into HANDHELD.MAP and write it (the CYD checks the hash before taking relay frames). */
  pushAddressMap(mapText: string, progress?: Progress): Promise<string>;
  /** Check a MAP folder (MAP.IDX, MAP.DAT, ...) the way hl_client.mapFolderFiles does. */
  mapFolderFiles?(files: Record<string, Uint8Array>): Array<[string, Uint8Array]>;
  /** Upload the offline road map into /MAP, resuming what the handheld already holds. */
  putMapFiles(files: Array<[string, Uint8Array]>,
    progress?: (name: string, held: number, size: number, done: number, total: number) => void): Promise<unknown>;
  /** Install a merged firmware image (the port is let go meanwhile and reopened after). */
  flash(image: Uint8Array, baud: number, progress: (p: FlashProgress) => void): Promise<void>;
  /** Stop the running transfer ("cancelled"). */
  cancel(): void;
}
