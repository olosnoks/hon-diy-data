/**
 * Display units for the UI: one ``U`` object, like the theme
 * (port of hondalog/ui/units.py).
 *
 * Everything behind the UI stays in the address map's units: the bus, the
 * recorder, the database, the exports. Only what is drawn is converted, here:
 *
 *     U.spec(spec)            the channel as the screen shows it: the spec with
 *                             the display unit, range, limits and decimals
 *     U.value(spec, v)        a bus value in the display unit
 *     U.text(spec, v)         ... formatted (no unit)
 *     U.withUnit(spec, v)     "190 °F"
 *     U.limit(spec, v)        a limit, "226.4 °F"
 *     U.convertText(text)     free text with numbers and units in it (an alarm
 *                             rule's own message): "Coolant 110 °C" -> "Coolant 230 °F"
 *
 * ``U.prefs === null`` means "as the address map has them": nothing is
 * converted. That is the state before the app loads its settings.
 *
 * The default preset is ``US``: mph, °F, gal/h, miles and AFR. Pressure stays
 * in kPa there, as in the Python app, because MAP in kPa is what every Honda
 * table, definition and tuning guide uses; psi, inHg or bar can be picked for
 * the MAP and baro readouts. Volts, %, ms and rpm have nothing to convert.
 */

import { pyFixed, pyG, pyRound } from '../expr/pynum';
import { UnitPrefs, canonical, choices, factors, quantity, type Quantity } from './units';

/** The two presets the menu offers. Both keep AFR (the map's mixture unit) and kPa. */
export const METRIC: Readonly<Record<Quantity, string>> = {
  temperature: '°C', pressure: 'kPa', speed: 'km/h', mixture: 'AFR', volume_flow: 'L/h', distance: 'km',
};
export const US: Readonly<Record<Quantity, string>> = {
  temperature: '°F', pressure: 'kPa', speed: 'mph', mixture: 'AFR', volume_flow: 'gal/h', distance: 'mi',
};
export const PRESETS: Readonly<Record<'US' | 'Metric', Readonly<Record<Quantity, string>>>> = { US, Metric: METRIC };
export type PresetName = keyof typeof PRESETS;
export const DEFAULT_PRESET: PresetName = 'US';

/** [settings key and UnitPrefs field, menu title, a unit of that quantity to list the choices of]. */
export const QUANTITIES: readonly (readonly [Quantity, string, string])[] = [
  ['speed', 'Speed', 'km/h'], ['temperature', 'Temperature', '°C'],
  ['pressure', 'Pressure (MAP, baro)', 'kPa'], ['mixture', 'Mixture', 'AFR'],
  ['volume_flow', 'Fuel flow', 'L/h'], ['distance', 'Distance', 'km'],
];

const LIMIT_KEYS = ['warn_lo', 'warn_hi', 'alarm_lo', 'alarm_hi'] as const;
// a number followed by a unit this module converts, as alarm messages write them
const IN_TEXT = /(?<![\w.])([-+−]?\d+(?:\.\d+)?)(\s*)(°C|°F|km\/h|mph|kPa|psi|inHg|L\/h|gal\/h)(\/s)?(?![\w/])/g;

/** What a channel spec needs for display conversion. Works with the address
 *  map's ChannelSpec / MathSpec (``min``/``max``) and with UI specs that add
 *  limits and a sign flag. */
export interface UnitSpec {
  units: string;
  decimals: number;
  min: number;
  max: number;
  kind?: string;
  signed?: boolean;
  warn_lo?: number | null;
  warn_hi?: number | null;
  alarm_lo?: number | null;
  alarm_hi?: number | null;
}

/** Display text of a value: true minus sign, optional +, '---' for no data,
 *  ON/OFF for flags (hondalog/ui/bus.py ChannelSpec.format). */
export function formatValue(spec: Pick<UnitSpec, 'decimals' | 'kind' | 'signed'>, value: number | null | undefined): string {
  if (value === null || value === undefined || value !== value) return '---';
  if (spec.kind === 'flag') return value >= 0.5 ? 'ON' : 'OFF';
  let v = value;
  if (Math.abs(v) < 0.5 * 10.0 ** -spec.decimals) v = 0.0;
  const text = pyFixed(Math.abs(v), spec.decimals);
  if (v < 0) return '−' + text;
  return spec.signed && v > 0 ? '+' + text : text;
}

/** Settings storage: ``units/<quantity>`` keys (localStorage, Tauri store ...). */
export interface UnitSettings {
  get(key: string): unknown;
  set(key: string, value: string): void;
}

interface Entry { shown: UnitSpec; scale: number; offset: number }

export class DisplayUnits {
  prefs: UnitPrefs | null = null;
  /** Bumps when the units change: widgets redo their texts. */
  revision = 0;
  private cache = new WeakMap<object, Entry>();
  private readonly listeners: (() => void)[] = [];

  // ---- choosing ----

  set(prefs: UnitPrefs | null): void {
    const same = prefs === null ? this.prefs === null : prefs.equals(this.prefs);
    if (same) return;
    this.prefs = prefs;
    this.cache = new WeakMap();
    this.revision += 1;
    for (const listener of [...this.listeners]) listener();
  }

  onChange(listener: () => void): () => void {
    this.listeners.push(listener);
    return () => {
      const i = this.listeners.indexOf(listener);
      if (i >= 0) this.listeners.splice(i, 1);
    };
  }

  setUnit(field: Quantity, unit: string): void {
    const current = this.asDict();
    current[field] = unit;
    this.set(new UnitPrefs(current));
  }

  preset(name: PresetName): void {
    this.set(new UnitPrefs(PRESETS[name]));
  }

  /** "US", "Metric" or "" for a mix of the two. */
  presetName(): PresetName | '' {
    const current = this.asDict();
    for (const name of Object.keys(PRESETS) as PresetName[]) {
      const units = PRESETS[name];
      if (QUANTITIES.every(([q]) => units[q] === current[q])) return name;
    }
    return '';
  }

  asDict(): Record<Quantity, string> {
    const prefs = this.prefs;
    return Object.fromEntries(QUANTITIES.map(([q]) => [q, prefs !== null ? prefs[q] : METRIC[q]])) as Record<Quantity, string>;
  }

  /** From settings (``units/<quantity>``). Unset or unreadable values take
   *  the default preset: US. */
  load(settings: Pick<UnitSettings, 'get'>): void {
    const units: Record<Quantity, string> = { ...PRESETS[DEFAULT_PRESET] };
    for (const [field, , base] of QUANTITIES) {
      const stored = settings.get(`units/${field}`);
      const name = stored ? canonical(String(stored)) : null;
      if (name !== null && quantity(name) === quantity(base)) units[field] = name;
    }
    this.set(new UnitPrefs(units));
  }

  save(settings: Pick<UnitSettings, 'set'>): void {
    for (const [field, unit] of Object.entries(this.asDict())) settings.set(`units/${field}`, unit);
  }

  // ---- converting ----

  /** The unit shown for a channel in ``baseUnit``. */
  unit(baseUnit: string): string {
    return this.prefs !== null ? this.prefs.displayUnit(baseUnit) : baseUnit;
  }

  private entry<T extends UnitSpec>(spec: T): Entry {
    const hit = this.cache.get(spec);
    if (hit) return hit;
    let shown: UnitSpec = spec;
    let scale = 1.0;
    let offset = 0.0;
    const target = this.unit(spec.units);
    if (target !== spec.units && spec.kind !== 'flag' && canonical(target) !== canonical(spec.units)) {
      [scale, offset] = factors(spec.units, target);
      // keep about the same resolution: psi needs a decimal more than kPa
      const extra = scale ? Math.max(-2, Math.min(2, pyRound(-Math.log10(Math.abs(scale))))) : 0;
      const limits: Partial<UnitSpec> = {};
      for (const key of LIMIT_KEYS) {
        const v = spec[key];
        if (v !== null && v !== undefined) limits[key] = v * scale + offset;
      }
      shown = { ...spec, units: target, decimals: Math.max(0, spec.decimals + extra),
        min: spec.min * scale + offset, max: spec.max * scale + offset, ...limits };
    }
    const entry = { shown, scale, offset };
    this.cache.set(spec, entry);
    return entry;
  }

  /** The channel as shown. Its numbers take display values (``U.value``). */
  spec<T extends UnitSpec>(spec: T): T {
    return this.entry(spec).shown as T;
  }

  /** [scale, offset]: display = bus value * scale + offset. */
  scale(spec: UnitSpec): [number, number] {
    const e = this.entry(spec);
    return [e.scale, e.offset];
  }

  value(spec: UnitSpec, value: number | null): number | null {
    if (value === null) return null;
    const e = this.entry(spec);
    return value * e.scale + e.offset;
  }

  text(spec: UnitSpec, value: number | null): string {
    const e = this.entry(spec);
    return formatValue(e.shown, value === null ? null : value * e.scale + e.offset);
  }

  withUnit(spec: UnitSpec, value: number | null): string {
    const e = this.entry(spec);
    return `${formatValue(e.shown, value === null ? null : value * e.scale + e.offset)} ${e.shown.units}`.trim();
  }

  /** A limit of the channel with its unit: "226.4 °F", "12.8 V". */
  limit(spec: UnitSpec, value: number): string {
    const e = this.entry(spec);
    const number = pyRound(value * e.scale + e.offset, e.scale !== 1.0 ? Math.max(e.shown.decimals, 1) : 6);
    return `${pyG(number)} ${e.shown.units}`.trim().replace(/-/g, '−');
  }

  /** Numbers with a convertible unit inside free text, converted. The number
   *  keeps its count of decimals; "x/s" rates convert as spans. */
  convertText(text: string): string {
    if (this.prefs === null || !text) return text;
    return text.replace(IN_TEXT, (whole, number: string, gap: string, unit: string, perS: string | undefined) => {
      const per = perS ?? '';
      const target = this.unit(unit);
      if (canonical(target) === canonical(unit)) return whole;
      let value = Number.parseFloat(number.replace('−', '-'));
      const [scale, offset] = factors(unit, target);
      value = value * scale + (per ? 0.0 : offset);           // a rate of change: scale only
      const decimals = number.includes('.') ? number.split('.')[1]!.length : 0;
      let shown = pyFixed(value, decimals);
      if (shown.startsWith('-') && number.includes('−')) shown = '−' + shown.slice(1);
      return `${shown}${gap}${target}${per}`;
    });
  }

  choices(baseUnit: string): string[] {
    return choices(baseUnit);
  }
}

/** The app's one display-units object. */
export const U = new DisplayUnits();
