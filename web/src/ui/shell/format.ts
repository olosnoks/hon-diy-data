/**
 * Display formatting for tiles. Storage stays metric; the user's unit choice
 * converts at display time. TODO(integrator): swap convert() for the units
 * module (src/core/units, agent #3) once it lands; the call sites stay.
 */
import type { ChannelSpec, ChannelState, MathSpec } from '../../core/types';
import type { UnitSystem } from '../../core/session';

type Spec = ChannelSpec | MathSpec;

export function isFlag(spec: Spec): boolean {
  return 'kind' in spec && spec.kind === 'flag';
}

export function isVerified(spec: Spec): boolean {
  // math channels are derived from their inputs: no address to verify
  return !('status' in spec) || spec.status === 'verified';
}

const IMPERIAL: Record<string, { units: string; scale: number; offset: number }> = {
  'km/h': { units: 'mph', scale: 0.621371, offset: 0 },
  km: { units: 'mi', scale: 0.621371, offset: 0 },
  '°C': { units: '°F', scale: 1.8, offset: 32 },
  kPa: { units: 'kPa', scale: 1, offset: 0 },
};

export function convert(value: number, units: string, system: UnitSystem): { value: number; units: string } {
  const c = system === 'imperial' ? IMPERIAL[units] : undefined;
  return c ? { value: value * c.scale + c.offset, units: c.units } : { value, units };
}

export function unitsOf(spec: Spec, system: UnitSystem): string {
  return convert(0, spec.units, system).units;
}

/** A number as the tile shows it: fixed decimals, thousands comma from 10,000, U+2212 minus. */
export function formatNumber(v: number, decimals: number, signed = false): string {
  const d = Math.max(0, Math.min(6, decimals));
  let s = Math.abs(v).toFixed(d);
  if (Math.abs(v) >= 10000) {
    const [int, frac] = s.split('.');
    s = int!.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (frac ? '.' + frac : '');
  }
  const neg = v < 0 && Number(s.replace(/,/g, '')) !== 0;
  return (neg ? '−' : signed && v > 0 ? '+' : '') + s;
}

/** A +/- quantity (trims): shown with a sign. */
function symmetric(spec: Spec): boolean {
  return spec.min < 0 && spec.min === -spec.max;
}

/** The tile's value text: "---" when stale, ON/OFF for flags. */
export function formatValue(st: ChannelState | undefined, system: UnitSystem, maxDecimals = 9): string {
  if (!st || st.value === null || !st.fresh) return '---';
  if (isFlag(st.spec)) return st.value >= 0.5 ? 'ON' : 'OFF';
  const { value } = convert(st.value, st.spec.units, system);
  const signed = symmetric(st.spec);
  return formatNumber(value, Math.min(st.spec.decimals, maxDecimals), signed);
}

export function formatBound(v: number | null, spec: Spec, system: UnitSystem, maxDecimals = 9): string {
  if (v === null || !Number.isFinite(v)) return '---';
  return formatNumber(convert(v, spec.units, system).value, Math.min(spec.decimals, maxDecimals), symmetric(spec));
}

export function mmss(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

/** Read rate as the status bar writes it (chrome.py _rate). */
export function rate(hz: number): string {
  return hz >= 1000 ? `${(hz / 1000).toFixed(1)} kHz` : hz < 100 ? `${hz.toFixed(1)} Hz` : `${hz.toFixed(0)} Hz`;
}
