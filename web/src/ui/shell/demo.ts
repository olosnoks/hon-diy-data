/**
 * Demo data source: plausible values from the design canvas (warm idle at
 * 812 / 824 rpm, trims +9 / +9, 14.1 V, CYD relaying) with a launch every
 * minute, so the shell, F1 Live and F2 Drive run without hardware.
 * Clearly simulated (ecu.simulated, session.demo). Remove the call in
 * main.tsx once the link / sim modules (agent #1) feed the store.
 */
import { useSession, type ActiveAlarm } from '../../core/session';
import type { ChannelSpec, ChannelState, ChannelStatus, ChannelKind } from '../../core/types';

let nextAddr = 0x10;
function ch(
  name: string, label: string, units: string, min: number, max: number, decimals: number,
  status: ChannelStatus, group: string, kind: ChannelKind = 'value', source?: string,
): ChannelSpec {
  const ecu = source === undefined && kind !== 'math';
  return {
    name, label, units, min, max, decimals, status, group, kind,
    addr: ecu ? nextAddr++ : 0, length: 1, formula: 'x', rate: 'fast', endian: 'big',
    ...(source ? { source } : {}),
  };
}

export const DEMO_SPECS: ChannelSpec[] = [
  ch('rpm', 'RPM', 'rpm', 0, 8000, 0, 'verified', 'engine'),
  ch('map', 'MAP', 'kPa', 0, 110, 0, 'verified', 'engine'),
  ch('baro', 'Baro', 'kPa', 60, 110, 0, 'unverified', ''),
  ch('tps', 'Throttle', '%', 0, 100, 0, 'verified', 'engine'),
  ch('load', 'Load', '%', 0, 100, 0, 'unverified', 'engine', 'math'),
  ch('ect', 'Coolant', '°C', -40, 130, 0, 'verified', 'engine'),
  ch('iat', 'Intake air', '°C', -40, 100, 0, 'verified', 'engine'),
  ch('idle_target', 'Idle target', 'rpm', 500, 1500, 0, 'unverified', 'idle'),
  ch('iac_cmd', 'IAC command', '', 0, 255, 0, 'unverified', 'idle'),
  ch('iacv', 'Idle valve', '%', 0, 100, 0, 'unverified', 'idle'),
  ch('iac_learn', 'IAC learn', '', 0, 255, 0, 'unverified', 'idle'),
  ch('ign_limit', 'RPM×MAP idx', '', 0, 255, 0, 'unverified', 'idle'),
  ch('vss', 'Speed', 'km/h', 0, 200, 0, 'verified', 'idle'),
  ch('stft', 'Short trim', '%', -30, 30, 1, 'verified', 'fuel'),
  ch('ltft', 'Long trim', '%', -30, 30, 1, 'verified', 'fuel'),
  ch('o2', 'O2 front', 'V', 0, 1.1, 2, 'verified', 'fuel'),
  ch('o2_post', 'O2 rear', 'V', 0, 1.1, 2, 'unverified', 'fuel'),
  ch('inj', 'Injector', 'ms', 0, 20, 2, 'unverified', 'fuel'),
  ch('purge_duty', 'Purge duty?', '', 0, 255, 0, 'unverified', 'fuel'),
  ch('ign', 'Timing', '°', -10, 50, 1, 'unverified', 'ignition'),
  ch('vtec', 'VTEC', '', 0, 1, 0, 'unverified', 'ignition', 'flag'),
  ch('vtec_psw', 'VTEC press. sw', '', 0, 1, 0, 'unverified', 'ignition', 'flag'),
  ch('knock', 'RPM idx 0x3C', '', 0, 255, 0, 'unverified', 'ignition'),
  ch('batt', 'Battery', 'V', 8, 16, 1, 'unverified', 'electrical'),
  ch('alt_fr', 'Alt field', '%', 0, 100, 0, 'unverified', 'electrical'),
  ch('eld', 'Elec load', 'A', 0, 80, 0, 'unverified', 'electrical'),
  ch('gps_speed', 'GPS speed', 'km/h', 0, 200, 0, 'verified', 'cyd', 'value', 'BB'),
  ch('gear', 'Gear', '', 0, 5, 0, 'unverified', 'cyd', 'math'),
  ch('trip', 'Trip', 'km', 0, 9999, 1, 'verified', 'cyd', 'value', 'BB'),
  ch('starter', 'Starter', '', 0, 1, 0, 'verified', 'switches', 'flag'),
  ch('brake', 'Brake', '', 0, 1, 0, 'unverified', 'switches', 'flag'),
  ch('psp', 'Steering load', '', 0, 1, 0, 'unverified', 'switches', 'flag'),
  ch('ac_sw', 'A/C switch', '', 0, 1, 0, 'verified', 'switches', 'flag'),
  ch('ac_clutch', 'A/C clutch', '', 0, 1, 0, 'verified', 'switches', 'flag'),
  ch('fuel_pump', 'Fuel pump', '', 0, 1, 0, 'verified', 'switches', 'flag'),
  ch('closed_loop', 'Closed loop', '', 0, 1, 0, 'verified', 'switches', 'flag'),
  ch('o2_heater', 'O2 heater?', '', 0, 1, 0, 'unverified', 'switches', 'flag'),
  ch('mil', 'MIL', '', 0, 1, 0, 'verified', 'switches', 'flag'),
];

const CYCLE = 60;

function wobble(t: number, a: number, f: number, phase = 0): number {
  return a * Math.sin(2 * Math.PI * f * t + phase);
}

/** The demo's values at t seconds since start (pure, deterministic). */
export function demoFrame(t: number): Record<string, number> {
  const c = t % CYCLE;
  const ect = Math.min(88, 71 + t / 30);
  const target = Math.round(895 - ect);
  let rpm: number, vss: number, tps: number, map: number, iac: number, brake = 0;
  if (c < 20 || c >= 46) {
    rpm = target - 12 + wobble(t, 9, 0.7) + wobble(t, 5, 2.3);
    vss = 0; tps = 0; map = 26 + wobble(t, 0.6, 0.5); iac = 89 + wobble(t, 2, 0.2);
  } else if (c < 21) {                       // the clutch bites with the throttle closed
    const k = c - 20;
    rpm = target - 12 - 280 * Math.sin(Math.PI * k);
    vss = 3 * k; tps = 0; map = 26 + 12 * Math.sin(Math.PI * k); iac = 89 + 18 * Math.sin(Math.PI * k);
  } else if (c < 22) {
    const k = c - 21;
    rpm = target - 12 + 1000 * k; vss = 3 + 5 * k; tps = 22 * k; map = 30 + 25 * k; iac = 95;
  } else if (c < 40) {
    const k = (c - 22) / 18;
    rpm = 2300 + wobble(c, 700, 0.18) + 300 * k; vss = 8 + 47 * Math.sin((Math.PI / 2) * k);
    tps = 26 + wobble(c, 8, 0.3); map = 58 + wobble(c, 9, 0.3); iac = 120;
  } else {
    const k = (c - 40) / 6;
    rpm = 2000 - 1100 * k; vss = 55 * (1 - k); tps = 0; map = 22; iac = 96 - 6 * k; brake = 1;
  }
  const fan = Math.floor(t / 30) % 2;
  const closed = tps > 30 ? 0 : 1;
  const gear = vss < 0.5 ? 0 : vss < 18 ? 1 : vss < 32 ? 2 : vss < 48 ? 3 : 4;
  const trip = 25.9 + demoTrip(t);
  return {
    rpm, map, baro: 100, tps, load: Math.min(100, (map / 100) * 85), ect, iat: 24,
    idle_target: target, iac_cmd: iac, iacv: Math.max(0, (iac - 70) / 3.3), iac_learn: 3,
    ign_limit: rpm > 1500 ? 4 : 0, vss,
    stft: 9.4 + wobble(t, 1.6, 0.9), ltft: 8.6, o2: 0.5 + wobble(t, 0.4, 1.1), o2_post: 0.94,
    inj: 2.44 + tps * 0.12, purge_duty: 27, ign: rpm > 1500 ? 28 : 12 + wobble(t, 1, 0.4),
    vtec: 0, vtec_psw: 0, knock: 1,
    batt: 14.1 + wobble(t, 0.05, 0.3) - (c > 19.8 && c < 20.6 ? 1.2 : 0),
    alt_fr: 48 + wobble(t, 4, 0.2) + fan * 10, eld: 15 + fan * 7.5,
    gps_speed: vss, gear, trip,
    starter: 0, brake, psp: 0, ac_sw: 0, ac_clutch: 0, fuel_pump: 1, closed_loop: closed, o2_heater: 1, mil: 0,
  };
}

function demoTrip(t: number): number {
  // average speed over a cycle is about 20 km/h: km = s * 20 / 3600
  return (t * 20) / 3600;
}

/** Feed the session store until the returned stop() is called. */
export function startDemo(hz = 10): () => void {
  const s = useSession.getState();
  const t0 = performance.now();
  const bounds: Record<string, { min: number; max: number }> = {};
  s.setDemo(true);
  s.setEcu({ state: 'ok', rateHz: 12.1, errors: 0, simulated: true, obd2: false });
  s.setCyd({ state: 'ok', mode: 'relay', sdLogging: true, mapMatch: true, mapHash: '8f3b48', sats: 12, fix: '3D', sdFreeGb: 1.2, sdDrive: 41 });
  s.setStall({ state: 'armed', bufferS: 0, preS: 20, postS: 5, count: 0 });
  if (!s.recording.active) s.startRecording();
  const mapped = new Set(DEMO_SPECS.filter((c) => c.addr).map((c) => c.addr));
  const rawAddrs: number[] = [];
  for (let a = 0x10; rawAddrs.length < 31; a++) if (!mapped.has(a)) rawAddrs.push(a);

  const tick = () => {
    const t = (performance.now() - t0) / 1000;
    const values = demoFrame(t);
    const states: ChannelState[] = DEMO_SPECS.map((spec) => {
      const v = values[spec.name] ?? 0;
      const b = (bounds[spec.name] ??= { min: v, max: v });
      b.min = Math.min(b.min, v);
      b.max = Math.max(b.max, v);
      return { spec, value: v, t, min: b.min, max: b.max, fresh: true, alarm: 0 };
    });
    const st = useSession.getState();
    st.updateChannels(states);
    const raw: Record<number, number> = {};
    rawAddrs.forEach((a, i) => { raw[a] = Math.floor(128 + 127 * Math.sin(t * 0.3 + i)) & 0xff; });
    st.setRaw(raw);
    const alarms: ActiveAlarm[] = [
      { name: 'ltft_high', channel: 'ltft', level: 'warn', message: 'Long trim high: total +18 %', acked: true },
    ];
    if ((values.rpm ?? 1000) < 600) {
      const prev = st.alarms.find((a) => a.name === 'idle_collapse');
      alarms.push({ name: 'idle_collapse', channel: 'rpm', level: 'alarm', message: 'Idle collapsing', acked: prev?.acked ?? false });
    }
    st.setAlarms(alarms);
    st.setEcu({ rateHz: 12.1 + Math.sin(t) * 0.3 });
    st.setStall({ bufferS: Math.min(20, t) });
  };
  tick();
  const id = setInterval(tick, 1000 / hz);
  return () => {
    clearInterval(id);
    useSession.getState().setDemo(false);
  };
}
