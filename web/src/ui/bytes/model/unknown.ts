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
const I = '2026-11-27/index-bytes.md';
const O = '2026-11-27/output-bytes.md';
const A = '2026-11-27/analog-bytes.md';
const M = '2026-11-27/idle-810.md';

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
  e(0x0b, 6, 'o2_heater', 'O2-sensor heater control output: off with the engine stopped, on from the first running read, off above ~3,500 rpm at MAP ≥ ~84 kPa (even in closed loop at 100 km/h); not ALTC (no voltage step); primary or secondary heater unknown', '43 zero reads: key-on, crank and 8 high-load episodes; ALT FR +1.3 % (~1-3 A) with it on, battery unchanged', 'medium', O),
  e(0x0c, 0, 'altc?', 'ALTC, alternator control (1 = normal charging): drops to 0 for a moment at a sudden electrical load step at idle', 'in those reads ALT FR rises half as much and the battery dips 2.5× deeper; three published tables name the bit Alt-C; ECM pin C2 ALTC exists', 'medium', O),
  e(0x0c, 3, 'vtec_psw', 'VTEC pressure switch (oil pressure feedback)', 'set in the same single read as 0x0A b2', 'low', D),
  e(0x0d, null, 'dead_0d', "dead on this car: 0 in all 22,658 reads (key-on, crank, A/C, brake, PSP, VTEC, WOT, decel cut); flags for hardware it does not have. The P2T 'bit 3 = engine-mount solenoid' cannot apply: the 1999-2000 P2P pinout has no such output", 'output-bytes.md; manual pinout p.361-364 (2026-11-27/ecu-io-checklist.md)', 'high', O),
  e(0x0e, null, 'dead_0e', 'dead on this car: 0 in all 22,658 reads; drop from the map after a cold-start log', 'output-bytes.md', 'high', O),
  e(0x0f, 0, 'closed_loop', '0 for ~14 s after start, in decel fuel cut and in WOT enrichment', '10.5 % of reads 0, matching 0x9A = 4', 'high', D),
  e(0x16, null, 'dead_16', 'dead: 0 in 8,682 reads (crank, WOT, 5,400 rpm, 107 km/h, decel cut, hours of idle); most likely an unused analog slot on this variant (a heater state would be a bit)', 'confirm on a cold-start log, then drop', 'high', O),
  e(0x1a, null, 'dead_1a_1f', '0x1A, 0x1B, 0x1D-0x1F: 0 in every read', 'idle and driving', 'high', D),
  e(0x1c, null, 'eld copy', 'copy of ELD (0x19)', 'equal in 3,198 of 3,198 reads, both passes', 'high', R),
  e(0x21, null, 'dead_21_23', '0x21, 0x23 (neighbours of LTFT): 0', 'idle and driving', 'high', D),
  e(0x27, null, 'ign_corr_27', 'ignition correction / limit in 1/4° counts (~0.23° advance per count at fixed rpm and MAP). Below 41 kPa an exact rpm quantiser 0-4 (steps of 58.7 rpm from 781, saturating at 1,016); 41-50 kPa a MAP ramp; above ~50 kPa a remembered state that drops on fast rpm rises and WOT and climbs back 0.2-0.7 counts/s: knock-control timing recovery. Not a table index', '24,500 reads over 34 drives', 'medium', I),
  e(0x28, null, 'iacv', 'IAC valve, relative: opens with the throttle off idle to ~200, dashpot decay 20 → 6 over ~15 s after a stop', 'Spearman 0.92 with TPS off idle', 'medium', D),
  e(0x29, null, 'iac_cmd', 'idle air control command before the output curve; 0x28 is a fixed, lagged function of it', 'r 0.95 with 0x28, same curve in both drives, moves first; hondamotor.ru lists IAC at 0x29', 'high', R),
  e(0x2a, null, 'iac_learn', 'learned idle air (IAC learn) in backup RAM: at fully warm idle a lagged copy of the IAC command, otherwise frozen or moving very slowly; survives key-off', 'output-bytes.md (medium-high)', 'medium', O),
  e(0x2b, null, 'dead_2b_31', '0x2B-0x2E, 0x30, 0x31, 0x34: 0', 'idle and driving', 'high', D),
  e(0x2f, null, 'purge_duty?', 'EVAP purge enable timer / ramp (not the duty itself): runs only with ECT above ~66-69 °C, ramps with airflow, cut in decel and on throttle moves, STFT falls as it rises; the ~810 rpm purge idle follows 5-12 s after it tops out; reset by a start, rpm > 1,000 or ECT < ~68 °C', 'idle-810: a 450-480 s rule gets 96.5-97 % of 6,893 idle seconds right; manual p.491: 810 ± 50 rpm = purging', 'medium', M),
  e(0x32, null, 'analog_32', 'live 8-bit sample of a fixed, ratiometric ~0.88 V input: an unused ECM input pin with a bias (or an internal reference); 45, single-read dips to 44 (4.2 %), independent of 0x37; not the EVAP tank pressure', 'meter check: key on, ECM C6 and A29 against C18 (~0.88 V at C6, ~2.5 V at A29)', 'medium', A),
  e(0x33, null, 'purge_ect_thr', 'constant 55 = 67.5 °C on the ECT scale: most likely the EVAP purge enable threshold (manual: 68 °C)', 'a cold-start log with 0x2F: purge must not start while ECT raw > 55', 'low', A),
  e(0x35, null, 'ect_thr_35', 'constant 45 = 75.6 °C on the ECT scale (warm-up or thermostat-open level?); never flickers', 'a cold-start log: a bit that changes as ECT raw falls through 45', 'low', A),
  e(0x36, null, 'const_36', 'constant 7: a calibration count or index (or a 0.14 V ECT short limit)', 'needs the ROM', 'none', A),
  e(0x37, null, 'analog_37', 'the same kind as 0x32 (a second biased input, or the same pin sampled crank-synchronously): 45, dips to 44 in 6.6 % of reads, fewer with rpm and load', 'analog-bytes.md', 'medium', A),
  e(0x38, null, 'const_38', '0x38 = 0x39 = 25 = 99.5 °C on the ECT scale: a stored constant (ECM fan or A/C-cut level?)', 'a long hot idle until the fan runs: does FANC switch at ECT raw ≈ 25?', 'low', A),
  e(0x3a, 0, 'post_start?', '0x3A = 25, bit 0 cleared for the first ~8 s after a start and back as the front O2 leaves its bias rail: a post-start / O2-active flag', 'a cold start from key-on: does the 24 period last as long as the O2 sits at 200?', 'low', A),
  e(0x3b, null, 'dead_3b', '0 in every read', 'idle and driving', 'high', D),
  e(0x3c, null, 'knock_level', 'knock-sensor background-noise level: noisy (each read independent), rises about with rpm^2.9, ignores load, the same in fuel cut; not a column index. The x/51 V formula is unverified', '23,300 reads; any step model leaves 19 % misclassified', 'medium', I),
  e(0x76, null, 'idle_target', 'idle target x/4 rpm (0x76-0x77); ECT-driven, −60 rpm while rolling at low ECT', 'rpm follows 2-3 s later, r 0.95', 'medium', R),
  e(0x78, null, 'ecu_id', 'ECU id / constants start here (not 0x76)', 'sweep-40-ff.md', 'medium', R),
  e(0x80, null, 'monitor_80_8c', '0x80-0x8C: per-monitor status / condition bit fields, high nibble unused; lead: bit 2 = incomplete (three bytes have it, three monitors are incomplete)', 'one read of 0x80-0x9F plus PID 01 at the next key-on', 'low', A),
  e(0x86, null, 'o2_monitor?', 'monitor status bits; lead: the O2 monitor (one of 0x86/0x88/0x8B should lose bit 2 when PID 01 clears a monitor). Earlier read as a small signed table following STFT', 'sweep-40-ff.md; analog-bytes.md section 3', 'low', A),
  e(0x8f, null, 'ltft_cell_2?', '142 = +10.9 %: a second long-term fuel-trim cell (LTFT 0x22 was 138-145 all evening)', 'read 0x80-0x9F after 20 min of steady cruising: a trim cell moves slowly and differently from 0x22', 'low', A),
  e(0x94, null, 'monitor_94_99', '0x94/0x95/0x96/0x99 = 2/15/4/5: 0x95 = supported non-continuous monitors (0x0F), 0x94 = complete mask (heater), 0x96 and 0x99 monitor bookkeeping or counters', 'PID 01 at the next key-on after highway driving', 'low', A),
  e(0x9a, null, 'fuel_status', 'OBD2 PID 03 encoding: 1 = open loop not ready, 2 = closed loop, 4 = open loop driving', '1 after start, 2 closed, 4 fuel cut and WOT', 'high', D),
  e(0x9b, null, 'dead_9b_9f', '0x9B, 0x9D-0x9F: 0', 'idle and driving', 'high', D),
  e(0x9c, null, 'load_ecu', 'calculated load (PID 04), 12.5-98 %', '98 % at WOT', 'medium', R),
  e(0xa0, null, 'o2_2', 'rear O2 (x / 51.3 V): 0.74 V median at cruise, independent of the front, 0.02 V in cut; 201 = start-up default', 'r 0.11 with the front at any lag on the highway', 'medium', D),
  e(0xa4, 0, 'xa4_b0', 'an idle-control status bit: on ~2 s after the radiator-fan load dropped off (rpm overshot), off ~46 s later with no visible trigger', 'one event, in the only full sweep', 'low', O),
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
