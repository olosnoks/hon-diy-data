/**
 * Where Replay and Events get drives: the app's drive store (wiring calls
 * setDriveSource(store) at start-up); until then, and in the tests, the
 * synthetic drives of fixture.ts stand in.
 */
import { create } from 'zustand';
import type { DriveStore } from '../../core/types';
import { FixtureStore } from './fixture';

export type DriveSource = Pick<DriveStore, 'listDrives' | 'loadDrive'>
  & Partial<Pick<DriveStore, 'deleteDrive' | 'importDb' | 'importFile' | 'setKeep' | 'loadEventNotes' | 'saveEventNote'>>;

interface SourceState { source: DriveSource; demo: boolean; version: number }

export const useDriveSource = create<SourceState>()(() => ({ source: new FixtureStore(), demo: true, version: 0 }));

/** Point Replay and Events at a drive store (demo = false: real drives). */
export function setDriveSource(source: DriveSource, demo = false): void {
  useDriveSource.setState((s) => ({ source, demo, version: s.version + 1 }));
}

/** Something changed in the store (a drive recorded, deleted, imported). */
export function drivesChanged(): void {
  useDriveSource.setState((s) => ({ version: s.version + 1 }));
}
