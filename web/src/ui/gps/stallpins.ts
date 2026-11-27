/**
 * "All stalls": every stall / near-stall / dropout capture in the drive
 * source with where it happened (the Python app's stall_locations view, done
 * over the DriveStore interface: each capture is loaded and its trigger is
 * placed on its own GPS track).
 */
import type { DriveInfo, LoadedDrive } from '../../core/types';
import type { DriveSource } from '../replay/source';
import { place, Track } from './geo/track';
import { driveWaypoints } from './geo/waypoints';
import type { StallPin } from './geo/stalls';

export const MAX_CAPTURES = 200;

export const isCapture = (d: DriveInfo) => d.kind === 'stall' || d.kind === 'dropout' || d.kind === 'capture' || d.kind === 'alarm';

/** The pin of one capture: its first stall / dropout / capture event, else its middle. */
export function pinOf(d: LoadedDrive): StallPin {
  const info = d.info;
  const w = driveWaypoints(d).find((x) => x.kind === 'stall' || x.kind === 'dropout' || x.kind === 'capture' || x.kind === 'nearstall');
  const rpm = d.series?.rpm;
  const tTrigger = w?.t ?? (rpm && rpm.t.length ? (rpm.t[0]! + rpm.t[rpm.t.length - 1]!) / 2 : 0);
  const track = Track.fromDrive(d);
  const where = place(track, tTrigger);
  let speed: number | null = null, max30: number | null = null;
  for (let i = 0; i < track.length; i++) {
    const dt = track.t[i]! - tTrigger;
    if (dt >= -30 && dt <= 0 && Number.isFinite(track.speed[i])) max30 = Math.max(max30 ?? 0, track.speed[i]!);
  }
  const pos = track.at(tTrigger);
  if (pos && Number.isFinite(pos.speedKmh)) speed = pos.speedKmh;
  return {
    name: info.name, kind: info.kind === 'stall' ? 'stall' : w?.kind ?? info.kind, reason: info.reason ?? '',
    message: w?.text ?? '', drive: info.name, wallUnix: info.startedUnix ? info.startedUnix + tTrigger : null, tTrigger,
    lat: where ? where[0] : null, lon: where ? where[1] : null, speedKmh: speed, maxSpeed30s: max30,
  };
}

/** Pins of the newest captures (at most MAX_CAPTURES), newest first. */
export async function loadStallPins(source: Pick<DriveSource, 'listDrives' | 'loadDrive'>, kinds: 'stalls' | 'all' = 'stalls'): Promise<StallPin[]> {
  const list = (await source.listDrives())
    .filter(isCapture)
    .filter((d) => kinds === 'all' || d.kind === 'stall')
    .sort((a, b) => b.startedUnix - a.startedUnix)
    .slice(0, MAX_CAPTURES);
  const out: StallPin[] = [];
  for (const d of list) {
    try {
      out.push(pinOf(await source.loadDrive(d.name)));
    } catch {
      out.push({ name: d.name, kind: d.kind, reason: d.reason ?? '', message: '', drive: d.name, wallUnix: d.startedUnix,
        tTrigger: 0, lat: null, lon: null, speedKmh: null, maxSpeed30s: null });
    }
  }
  return out;
}
