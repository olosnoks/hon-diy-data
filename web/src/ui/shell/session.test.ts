import { beforeEach, describe, expect, it } from 'vitest';
import {
  alarmLevel, alarmUnacked, HISTORY_N, initialSession, resetSession, SCREENS, useSession,
} from '../../core/session';
import type { ChannelSpec, ChannelState } from '../../core/types';

const spec: ChannelSpec = {
  name: 'rpm', label: 'RPM', addr: 0x10, length: 2, formula: 'x', units: 'rpm', min: 0, max: 8000,
  decimals: 0, kind: 'value', status: 'verified', rate: 'fast', endian: 'big',
};
const st = (value: number | null, t: number, fresh = true): ChannelState =>
  ({ spec, value, t, min: value, max: value, fresh, alarm: 0 });

beforeEach(() => {
  localStorage.clear();
  resetSession();
});

describe('session store', () => {
  it('starts on F1 Live, Dracula, metric, nothing connected', () => {
    const s = useSession.getState();
    expect(s.screen).toBe('live');
    expect(s.theme).toBe('dracula');
    expect(s.units).toBe('metric');
    expect(s.ecu.state).toBe('stopped');
    expect(s.cyd.state).toBe('off');
    expect(s.recording.active).toBe(false);
    expect(SCREENS.map((x) => x.key)).toEqual(['F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8']);
    expect(SCREENS.map((x) => x.name)).toEqual(['Live', 'Drive', 'Events', 'Replay', 'Power', 'GPS', 'Bytes', 'Setup']);
  });

  it('toggles the theme between Dracula and Darker only', () => {
    const s = useSession.getState();
    s.toggleTheme();
    expect(useSession.getState().theme).toBe('darker');
    s.toggleTheme();
    expect(useSession.getState().theme).toBe('dracula');
  });

  it('persists theme, units and screen', () => {
    const s = useSession.getState();
    s.setTheme('darker');
    s.setUnits('imperial');
    s.setScreen('drive');
    expect(JSON.parse(localStorage.getItem('hondalog.prefs')!)).toEqual({ theme: 'darker', units: 'imperial', screen: 'drive' });
  });

  it('merges partial status updates', () => {
    const s = useSession.getState();
    s.setEcu({ state: 'ok', rateHz: 12.1 });
    s.setEcu({ errors: 3 });
    expect(useSession.getState().ecu).toMatchObject({ state: 'ok', rateHz: 12.1, errors: 3, simulated: false });
    s.setCyd({ state: 'ok', mode: 'relay', mapMatch: true });
    expect(useSession.getState().cyd).toMatchObject({ state: 'ok', mode: 'relay', mapMatch: true, sdLogging: false });
  });

  it('samples channel history at 2 Hz and caps it at 60 s', () => {
    const s = useSession.getState();
    s.updateChannels([st(800, 0)]);
    s.updateChannels([st(810, 0.1)]); // too soon: value updates, history does not
    expect(useSession.getState().channels.rpm!.value).toBe(810);
    expect(useSession.getState().history.rpm!.v).toEqual([800]);
    s.updateChannels([st(820, 0.5)]);
    s.updateChannels([st(null, 1.0, false)]);
    expect(useSession.getState().history.rpm!.v).toEqual([800, 820, null]);
    for (let i = 3; i < 200; i++) s.updateChannels([st(i, i * 0.5)]);
    const h = useSession.getState().history.rpm!;
    expect(h.v.length).toBe(HISTORY_N);
    expect(h.v[h.v.length - 1]).toBe(199);
  });

  it('restarts history when time goes backwards (reconnect)', () => {
    const s = useSession.getState();
    s.updateChannels([st(800, 10)]);
    s.updateChannels([st(900, 1)]);
    expect(useSession.getState().history.rpm!.v).toEqual([900]);
  });

  it('marks, and recording start / stop', () => {
    const s = useSession.getState();
    expect(s.mark()).toBe(1);
    expect(s.mark('stumble')).toBe(2);
    expect(useSession.getState().marks[1]!.text).toBe('stumble');
    s.setStall({ count: 2 });
    s.startRecording();
    const rec = useSession.getState().recording;
    expect(rec.active).toBe(true);
    expect(rec.startedUnixMs).toBeTypeOf('number');
    expect(useSession.getState().stall.count).toBe(0);
    s.stopRecording();
    expect(useSession.getState().recording).toEqual(initialSession().recording);
  });

  it('Esc closes the open panel first, then acknowledges alarms', () => {
    const s = useSession.getState();
    s.setAlarms([{ name: 'a', channel: 'rpm', level: 'alarm', message: 'x', acked: false }]);
    s.openPanel('raw');
    s.escape();
    expect(useSession.getState().panel).toBeNull();
    expect(useSession.getState().alarms[0]!.acked).toBe(false);
    s.escape();
    expect(useSession.getState().alarms[0]!.acked).toBe(true);
  });

  it('togglePanel opens and closes the same panel', () => {
    const s = useSession.getState();
    s.togglePanel('raw');
    expect(useSession.getState().panel).toBe('raw');
    s.togglePanel('raw');
    expect(useSession.getState().panel).toBeNull();
  });

  it('alarm level of a channel is the worst of its state and the active alarms', () => {
    const s = useSession.getState();
    s.updateChannels([st(800, 0)]);
    expect(alarmLevel(useSession.getState(), 'rpm')).toBe(0);
    s.setAlarms([{ name: 'w', channel: 'rpm', level: 'warn', message: '', acked: true }]);
    expect(alarmLevel(useSession.getState(), 'rpm')).toBe(1);
    expect(alarmUnacked(useSession.getState(), 'rpm')).toBe(false);
    s.setAlarms([
      { name: 'w', channel: 'rpm', level: 'warn', message: '', acked: true },
      { name: 'a', channel: 'rpm', level: 'alarm', message: '', acked: false },
    ]);
    expect(alarmLevel(useSession.getState(), 'rpm')).toBe(2);
    expect(alarmUnacked(useSession.getState(), 'rpm')).toBe(true);
    expect(alarmLevel(useSession.getState(), 'ect')).toBe(0);
  });

  it('clearChannels forgets values, history and raw bytes', () => {
    const s = useSession.getState();
    s.updateChannels([st(800, 0)]);
    s.setRaw({ 0x20: 7 });
    s.clearChannels();
    const n = useSession.getState();
    expect(n.channels).toEqual({});
    expect(n.history).toEqual({});
    expect(n.raw).toEqual({});
  });
});
