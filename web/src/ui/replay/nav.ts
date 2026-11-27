/**
 * Requests between screens: Events -> "Open in Replay", "Show on GPS map";
 * Replay -> "Open in Power". The asking screen sets a request and switches
 * screens; the target screen takes it (seq tells a new request from an old one).
 * TODO(integrator): these belong in src/core/session.ts (see the final report);
 * F6 GPS (#10) and F5 Power (#10) read gpsFocus / powerFocus from here until then.
 */
import { create } from 'zustand';
import { useSession } from '../../core/session';

export interface DriveMoment { drive: string; t: number; seq: number }
export interface DriveSpan { drive: string; t0: number; t1: number; seq: number }

interface Nav {
  replayRequest: DriveMoment | null;
  gpsFocus: DriveMoment | null;
  powerFocus: DriveSpan | null;
  /** the drive open in Replay (Events selects it first) */
  replayDrive: string | null;
}

let seq = 0;
export const useReplayNav = create<Nav>()(() => ({ replayRequest: null, gpsFocus: null, powerFocus: null, replayDrive: null }));

export function openInReplay(drive: string, t: number): void {
  useReplayNav.setState({ replayRequest: { drive, t, seq: ++seq } });
  useSession.getState().setScreen('replay');
}

export function showOnMap(drive: string, t: number): void {
  useReplayNav.setState({ gpsFocus: { drive, t, seq: ++seq } });
  useSession.getState().setScreen('gps');
}

export function openInPower(drive: string, t0: number, t1: number): void {
  useReplayNav.setState({ powerFocus: { drive, t0, t1, seq: ++seq } });
  useSession.getState().setScreen('power');
}
