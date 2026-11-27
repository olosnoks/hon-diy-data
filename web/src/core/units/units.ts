/**
 * Display units (port of hondalog/units.py). Channels and logs keep their
 * base unit; this converts for the screen only (°C/°F, kPa/psi/inHg,
 * km/h/mph, lambda/AFR ...).
 *
 *     const prefs = new UnitPrefs({ temperature: '°F', pressure: 'psi' });
 *     prefs.convert(88.0, '°C');          // [190.4, '°F']
 *     prefs.format(88.0, '°C', 0);        // "190 °F"
 */

import { pyFixed, pyRepr } from '../expr/pynum';

export const STOICH_PETROL = 14.7;

export type Quantity = 'temperature' | 'pressure' | 'speed' | 'mixture' | 'volume_flow' | 'distance';

/** quantity -> {unit: [scale, offset]} with display = base * scale + offset,
 *  where "base" is the first unit listed for the quantity. */
export const TABLE: Readonly<Record<Quantity, Readonly<Record<string, readonly [number, number]>>>> = {
  temperature: { '°C': [1.0, 0.0], '°F': [1.8, 32.0], K: [1.0, 273.15] },
  pressure: { kPa: [1.0, 0.0], psi: [0.1450377, 0.0], inHg: [0.2952998, 0.0], bar: [0.01, 0.0], mmHg: [7.500617, 0.0] },
  speed: { 'km/h': [1.0, 0.0], mph: [0.6213712, 0.0] },
  mixture: { lambda: [1.0, 0.0], AFR: [STOICH_PETROL, 0.0] },
  volume_flow: { 'L/h': [1.0, 0.0], 'gal/h': [0.2641721, 0.0] },
  distance: { km: [1.0, 0.0], mi: [0.6213712, 0.0] },
};

export const QUANTITY_NAMES = Object.keys(TABLE) as Quantity[];

const ALIASES: Record<string, string> = {
  c: '°C', degc: '°C', '°c': '°C', f: '°F', degf: '°F', '°f': '°F',
  kpa: 'kPa', psi: 'psi', inhg: 'inHg', bar: 'bar', mmhg: 'mmHg',
  'km/h': 'km/h', kph: 'km/h', kmh: 'km/h', mph: 'mph', lambda: 'lambda',
  'λ': 'lambda', afr: 'AFR', 'l/h': 'L/h', 'gal/h': 'gal/h', km: 'km', mi: 'mi',
  k: 'K',
};

const QUANTITY: Record<string, Quantity> = {};
for (const q of QUANTITY_NAMES) for (const unit of Object.keys(TABLE[q])) QUANTITY[unit] = q;

const own = (o: object, k: string) => Object.prototype.hasOwnProperty.call(o, k);

/** The table's spelling of a unit, or null if it is not one we convert. */
export function canonical(unit: string): string | null {
  const key = unit.trim().toLowerCase().replace(/ /g, '');
  return own(ALIASES, key) ? ALIASES[key]! : null;
}

/** 'temperature', 'pressure' ... or null for units that are shown as-is. */
export function quantity(unit: string): Quantity | null {
  const name = canonical(unit);
  return name ? QUANTITY[name] ?? null : null;
}

/** Display units on offer for a base unit ([] when it cannot be converted). */
export function choices(unit: string): string[] {
  const q = quantity(unit);
  return q ? Object.keys(TABLE[q]) : [];
}

function pair(unit: string, to: string): [string, string, Quantity] {
  const src = canonical(unit);
  const dst = canonical(to);
  if (src === null || dst === null || QUANTITY[src] !== QUANTITY[dst]) {
    throw new RangeError(`cannot convert ${pyRepr(unit)} to ${pyRepr(to)}`);
  }
  return [src, dst, QUANTITY[src]!];
}

/** Convert between two units of the same quantity. Throws RangeError otherwise. */
export function convert(value: number | null, unit: string, to: string): number | null {
  const [src, dst, q] = pair(unit, to);
  if (value === null) return null;
  const table = TABLE[q];
  const [s0, o0] = table[src]!;
  const base = (value - o0) / s0;
  const [s1, o1] = table[dst]!;
  return base * s1 + o1;
}

/** [scale, offset] with value in ``to`` = value in ``unit`` * scale + offset. */
export function factors(unit: string, to: string): [number, number] {
  const [src, dst, q] = pair(unit, to);
  const [s0, o0] = TABLE[q][src]!;
  const [s1, o1] = TABLE[q][dst]!;
  const scale = s1 / s0;
  return [scale, o1 - o0 * scale];
}

/** Convert a difference (5 °C wide = 9 °F wide): scale only, no offset. */
export function convertSpan(span: number, unit: string, to: string): number {
  const [src, dst, q] = pair(unit, to);
  return span / TABLE[q][src]![0] * TABLE[q][dst]![0];
}

export type UnitPrefsInit = Partial<Record<Quantity, string>>;

/** The user's display unit per quantity. Defaults are the map's base units. */
export class UnitPrefs {
  temperature = '°C';
  pressure = 'kPa';
  speed = 'km/h';
  mixture = 'lambda';
  volume_flow = 'L/h';
  distance = 'km';

  constructor(init: UnitPrefsInit = {}) {
    for (const q of QUANTITY_NAMES) {
      const given = init[q] ?? this[q];
      const unit = canonical(given);
      if (unit === null || QUANTITY[unit] !== q) throw new RangeError(`${pyRepr(given)} is not a unit of ${q}`);
      this[q] = unit;
    }
  }

  /** Known quantities only; anything else in the table is ignored. */
  static fromMapping(table: Readonly<Record<string, string>>): UnitPrefs {
    const init: UnitPrefsInit = {};
    for (const q of QUANTITY_NAMES) if (own(table, q)) init[q] = table[q];
    return new UnitPrefs(init);
  }

  static us(): UnitPrefs {
    return new UnitPrefs({ temperature: '°F', pressure: 'psi', speed: 'mph', mixture: 'AFR', volume_flow: 'gal/h', distance: 'mi' });
  }

  asDict(): Record<Quantity, string> {
    return Object.fromEntries(QUANTITY_NAMES.map((q) => [q, this[q]])) as Record<Quantity, string>;
  }

  equals(other: UnitPrefs | null | undefined): boolean {
    return !!other && QUANTITY_NAMES.every((q) => this[q] === other[q]);
  }

  /** The unit to show for a channel whose base unit is ``baseUnit``. */
  displayUnit(baseUnit: string): string {
    const q = quantity(baseUnit);
    return q ? this[q] : baseUnit;
  }

  /** [display value, display unit]. Unknown units pass through unchanged. */
  convert(value: number | null, baseUnit: string): [number | null, string] {
    const q = quantity(baseUnit);
    if (q === null) return [value, baseUnit];
    const target = this[q];
    return [convert(value, baseUnit, target), target];
  }

  format(value: number | null, baseUnit: string, decimals = 0): string {
    const [shown, unit] = this.convert(value, baseUnit);
    if (shown === null || shown !== shown) return '--';
    return `${pyFixed(shown, decimals)} ${unit}`.trimEnd();
  }
}
