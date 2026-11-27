/**
 * The unknown-bytes list: every RAM byte (or flag bit) the map does not
 * decode with confidence, what the analysis says about it, and the owner's
 * own notes. Seeded from docs/analysis/2026-11-26/README.md (verdict per
 * byte) and drive-bytes.md (bytes alive while driving, flag bits, rename
 * candidates). The owner's notes and confidence edits are kept in storage.
 */

export type Confidence = 'high' | 'medium' | 'low' | 'none';
export const CONFIDENCES: Confidence[] = ['high', 'medium', 'low', 'none'];

export interface UnknownEntry {
  /** "0x29" or "0x0B.6" for a bit. */
  key: string;
  addr: number;
  bit: number | null;
  /** The best guess at a name ("iac_cmd", "purge_duty?"). */
  name: string;
  what: string;
  evidence: string;
  confidence: Confidence;
  /** The owner's notes (empty in the seed). */
  notes: string;
  source: string;
}

const R = 'README.md';
const D = 'drive-bytes.md';

function e(addr: number, bit: number | null, name: string, what: string, evidence: string, confidence: Confidence, source: string): UnknownEntry {
  const key = `0x${addr.toString(16).toUpperCase().padStart(2, '0')}${bit === null ? '' : '.' + bit}`;
  return { key, addr, bit, name, what, evidence, confidence, notes: '', source };
}

export const SEED: UnknownEntry[] = [
  e(0x03, null, 'dead_03_07', '0x03-0x07: 0 in every read, driving included', '3,198 slow reads incl. WOT, interstate, decel cut', 'high', D),
  e(0x08, 0, 'starter', 'starter input', 'set in the first cranking read only', 'high', D),
  e(0x08, 2, 'psp', 'P/S pressure switch, 0 = steering loaded (formula 1 - bit(x, 2))', '18 zeros, all junction turns or parking; never with yaw < 10°/s', 'high', D),
  e(0x08, 3, 'brake', 'brake switch', 'set in 97 % of reads decelerating harder than 4 km/h/s', 'high', D),
  e(0x08, 7, 'not clutch/gear', 'always 1: all gears, neutral, every clutch take-up; probably the A/T P/N input fixed on the M/T', 'constant; manual: no clutch or gear input on this ECM', 'high', D),
  e(0x09, null, 'scs?', 'all bits 0; literature b3 = SCS (service check)', 'never jumped while driving', 'low', D),
  e(0x0a, 2, 'vtec', 'VTEC solenoid on', '1 in one read at 5,376 rpm WOT (23:03:11.70), 0 at 4,786', 'medium', D),
  e(0x0a, 7, 'ecu_ready?', 'always 1 with the key on', 'flag-bits.md', 'low', D),
  e(0x0b, 6, 'o2_heater?', 'off above ~3,500 rpm at MAP ≥ 84 kPa (hysteresis), on from cranking: not "engine turning"; primary HO2S heater?', '26 zero reads, all high load and rpm', 'medium', D),
  e(0x0c, 0, 'elec load / alt ctrl?', 'always 1 while driving (a single-read drop at idle before)', 'flag-bits.md', 'low', D),
  e(0x0c, 3, 'vtec_psw', 'VTEC pressure switch (oil pressure feedback)', 'set in the same single read as 0x0A b2', 'low', D),
  e(0x0d, null, 'flags_0d?', 'probably a flag byte (P2T: bit 3 = engine-mount solenoid); 0 at idle, key-on, crank, driving', 'no change seen', 'low', R),
  e(0x0e, null, 'flags_0e?', 'probably a flag byte; 0 everywhere so far', 'no change seen', 'low', R),
  e(0x0f, 0, 'closed_loop', '0 for ~14 s after start, in decel fuel cut and in WOT enrichment', '10.5 % of reads 0, matching 0x9A = 4', 'high', D),
  e(0x16, null, 'dead_16', '0 in every read', 'idle and driving', 'high', D),
  e(0x1a, null, 'dead_1a_1f', '0x1A, 0x1B, 0x1D-0x1F: 0 in every read', 'idle and driving', 'high', D),
  e(0x1c, null, 'eld copy', 'copy of ELD (0x19)', 'equal in 3,198 of 3,198 reads, both passes', 'high', R),
  e(0x21, null, 'dead_21_23', '0x21, 0x23 (neighbours of LTFT): 0', 'idle and driving', 'high', D),
  e(0x27, null, 'x27_rpm_map_idx', 'rpm band at idle (0 below ~845, +1 per ~50 rpm), MAP term above ~42 kPa (~+0.85/kPa); not an ignition limit', 'r 0.83 with rpm; adds nothing to an ignition fit', 'low', D),
  e(0x28, null, 'iacv', 'IAC valve, relative: opens with the throttle off idle to ~200, dashpot decay 20 → 6 over ~15 s after a stop', 'Spearman 0.92 with TPS off idle', 'medium', D),
  e(0x29, null, 'iac_cmd', 'idle air control command before the output curve; 0x28 is a fixed, lagged function of it', 'r 0.95 with 0x28, same curve in both drives, moves first; hondamotor.ru lists IAC at 0x29', 'high', R),
  e(0x2a, null, 'iac_learn', 'slow learned value, steps ±1 every 13-30 min at idle; frozen off idle', 'no sensor follows it; 3 for 50 min of driving', 'medium', D),
  e(0x2b, null, 'dead_2b_31', '0x2B-0x2E, 0x30, 0x31, 0x34: 0', 'idle and driving', 'high', D),
  e(0x2f, null, 'purge_duty?', 'ramps from 19-20 with airflow, 0 in decel and on throttle change, STFT falls as it rises; at a standstill a ~31-33 s ticker', 'Spearman −0.74 with STFT on the highway; 68 °C enable matches PCS', 'medium', D),
  e(0x32, null, 'counter_32', 'steady 45 flickering to 44 (bit 0), tracks nothing', 'idle and driving', 'low', R),
  e(0x33, null, 'const_55', 'constant 55 (0x35 = 45, 0x36 = 7 at idle)', 'never changed', 'high', R),
  e(0x37, null, 'counter_37', 'steady 45 flickering to 44, dips more below 1,500 rpm', 'idle and driving', 'low', D),
  e(0x38, null, 'const_25', '0x38, 0x39 = 25; 0x3A = 25 (24 for 8 s after the start)', 'never changed otherwise', 'high', D),
  e(0x3b, null, 'dead_3b', '0 in every read', 'idle and driving', 'high', D),
  e(0x3c, null, 'x3c_rpm_idx', 'a step function of rpm only, 1-11 (mapped as "knock"); maybe the knock-sensor noise level', 'Spearman 0.78 with rpm, flat in MAP', 'low', D),
  e(0x76, null, 'idle_target', 'idle target x/4 rpm (0x76-0x77); ECT-driven, −60 rpm while rolling at low ECT', 'rpm follows 2-3 s later, r 0.95', 'medium', R),
  e(0x78, null, 'ecu_id', 'ECU id / constants start here (not 0x76)', 'sweep-40-ff.md', 'medium', R),
  e(0x86, null, 'stft_table?', 'small signed table that follows STFT', 'sweep-40-ff.md', 'low', R),
  e(0x9a, null, 'fuel_status', 'OBD2 PID 03 encoding: 1 = open loop not ready, 2 = closed loop, 4 = open loop driving', '1 after start, 2 closed, 4 fuel cut and WOT', 'high', D),
  e(0x9b, null, 'dead_9b_9f', '0x9B, 0x9D-0x9F: 0', 'idle and driving', 'high', D),
  e(0x9c, null, 'load_ecu', 'calculated load (PID 04), 12.5-98 %', '98 % at WOT', 'medium', R),
  e(0xa0, null, 'o2_2', 'rear O2 (x / 51.3 V): 0.74 V median at cruise, independent of the front, 0.02 V in cut; 201 = start-up default', 'r 0.11 with the front at any lag on the highway', 'medium', D),
  e(0xa4, 0, 'idle state?', 'candidate idle-state flag', 'flag-bits.md', 'low', R),
];

export interface NotesStore {
  load(): Record<string, Partial<Pick<UnknownEntry, 'notes' | 'confidence' | 'name'>>>;
  save(data: Record<string, Partial<Pick<UnknownEntry, 'notes' | 'confidence' | 'name'>>>): void;
}

const KEY = 'hondalog.bytes.unknown';

export const localNotesStore: NotesStore = {
  load() {
    try {
      return JSON.parse(globalThis.localStorage?.getItem(KEY) ?? '{}');
    } catch {
      return {};
    }
  },
  save(data) {
    try { globalThis.localStorage?.setItem(KEY, JSON.stringify(data)); } catch { /* storage full or off */ }
  },
};

/** The seed with the owner's edits applied. */
export function mergedEntries(store: NotesStore, seed: UnknownEntry[] = SEED): UnknownEntry[] {
  const edits = store.load();
  return seed.map((s) => ({ ...s, ...(edits[s.key] ?? {}) }));
}

export function editEntry(store: NotesStore, key: string, change: Partial<Pick<UnknownEntry, 'notes' | 'confidence' | 'name'>>): void {
  const all = store.load();
  all[key] = { ...(all[key] ?? {}), ...change };
  store.save(all);
}
