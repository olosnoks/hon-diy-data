/**
 * The drive F5 Power and F6 GPS work on: the list from the shared drive
 * source (the same one Replay and Events use), the chosen drive loaded.
 * Starts on the drive open in Replay, else the newest whole drive.
 */
import { useEffect, useState } from 'react';
import type { DriveInfo, LoadedDrive } from '../../core/types';
import { useDriveSource } from '../replay/source';
import { useReplayNav } from '../replay/nav';

export interface DriveState {
  drives: DriveInfo[];
  name: string | null;
  drive: LoadedDrive | null;
  loading: boolean;
  error: string | null;
  demo: boolean;
  open(name: string): void;
}

export function newestDrive(drives: DriveInfo[]): string | null {
  const whole = drives.filter((d) => d.kind === 'drive');
  const pool = whole.length ? whole : drives;
  return pool.length ? [...pool].sort((a, b) => b.startedUnix - a.startedUnix)[0]!.name : null;
}

export function useDrive(want: string | null = null): DriveState {
  const { source, demo, version } = useDriveSource();
  const [drives, setDrives] = useState<DriveInfo[]>([]);
  const [name, setName] = useState<string | null>(null);
  const [drive, setDrive] = useState<LoadedDrive | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    source.listDrives().then((list) => {
      if (!live) return;
      setDrives(list);
      setName((cur) => {
        if (cur && list.some((d) => d.name === cur)) return cur;
        const replay = useReplayNav.getState().replayDrive;
        if (replay && list.some((d) => d.name === replay)) return replay;
        return newestDrive(list);
      });
    }).catch((e: unknown) => live && setError(String(e)));
    return () => { live = false; };
  }, [source, version]);

  useEffect(() => {
    if (want) setName(want);
  }, [want]);

  useEffect(() => {
    if (!name) { setDrive(null); return; }
    let live = true;
    setLoading(true);
    setError(null);
    source.loadDrive(name).then((d) => { if (live) setDrive(d); })
      .catch((e: unknown) => { if (live) { setDrive(null); setError(String(e instanceof Error ? e.message : e)); } })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [source, name, version]);

  return { drives, name, drive, loading, error, demo, open: setName };
}
