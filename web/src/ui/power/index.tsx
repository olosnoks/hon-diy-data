/**
 * F5 Power: a dyno from a logged pull (design canvas "3 · F5 Power").
 *
 * Left, the pulls found automatically (one gear, throttle held, rpm rising)
 * with their quality; middle, the curve (torque and hp vs rpm with the band,
 * the dashed D16Y8 reference, the VTEC zone and the highest rpm logged), the
 * pull trace, or time in cell (the old Map 3D, flat); right, the result, the
 * inputs (editable), compare, baseline, CSV. Numbers are "measured +
 * modelled": the acceleration is measured, mass, losses, road load and grade
 * are assumptions.
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import type { LoadedDrive } from '../../core/types';
import { useTheme } from '../shell/useTheme';
import { FONTS, type Theme } from '../theme';
import { useReplayNav } from '../replay/nav';
import { saveText, type SaveFn } from '../gps/save';
import {
  analyseDrive, computeResult, curveCsv, DEFAULT_INPUTS, loadBaseline, ordinal, pullDetail, QUALITY_LABEL, REFERENCE_D16Y8,
  saveBaseline, thousands, tyreRadius, validInputs, type Baseline, type PowerInputs, type PowerResult, type PullItem,
} from './analysis';
import { buildCells } from './cells';
import CellView from './CellMap';
import { curveData, curveOptions, traceData, traceOptions, type Overlay } from './charts';
import { UPlotChart } from './UPlotChart';
import { useDrive } from './useDrive';
import { Button, DriveSelect, Empty, H2, inputStyle, ResultCard, Row, Section, Swatch, Tabs } from './widgets';

type Tab = 'curve' | 'trace' | 'cells';
const INPUTS_KEY = 'hondalog.power.inputs';

function loadInputs(): PowerInputs {
  try {
    const raw = localStorage.getItem(INPUTS_KEY);
    const v = raw ? { ...DEFAULT_INPUTS, ...(JSON.parse(raw) as Partial<PowerInputs>) } : DEFAULT_INPUTS;
    return validInputs(v) ? DEFAULT_INPUTS : v;
  } catch {
    return DEFAULT_INPUTS;
  }
}

export function wallClock(drive: LoadedDrive | null, t: number): string {
  const start = drive?.info?.startedUnix;
  if (!start) return `${t.toFixed(1)} s`;
  const d = new Date((start + t) * 1000);
  return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':');
}

const qualityColour = (t: Theme, q: PullItem['quality']) => (q === 'full' ? t.ok : q === 'part' ? t.unverified : t.comment);

type Draft = Record<keyof PowerInputs, string>;

function draftOf(i: PowerInputs): Draft {
  return {
    massKg: String(i.massKg), tyre: i.tyre, drivetrainLossPct: String(i.drivetrainLossPct), rollN: String(i.rollN),
    aeroK: String(i.aeroK), gradePct: i.gradePct == null ? '' : String(i.gradePct),
  };
}

function parseDraft(d: Draft): PowerInputs | null {
  const num = (s: string) => (s.trim() === '' ? NaN : Number(s.replace(',', '.')));
  const v: PowerInputs = {
    massKg: num(d.massKg), tyre: d.tyre, drivetrainLossPct: num(d.drivetrainLossPct), rollN: num(d.rollN), aeroK: num(d.aeroK),
    gradePct: d.gradePct.trim() === '' ? null : num(d.gradePct),
  };
  if ([v.massKg, v.drivetrainLossPct, v.rollN, v.aeroK].some((x) => !Number.isFinite(x))) return null;
  if (v.gradePct != null && !Number.isFinite(v.gradePct)) return null;
  return v;
}

function NumField({ t, label, value, onChange, unit, width = 70, placeholder }: {
  t: Theme; label: string; value: string; onChange(v: string): void; unit?: string; width?: number; placeholder?: string;
}) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <input aria-label={label} inputMode="decimal" value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} style={inputStyle(t, width)} />
      {unit && <span>{unit}</span>}
    </span>
  );
}

export interface PowerViewProps {
  t: Theme;
  drive: LoadedDrive | null;
  /** select the pull that overlaps this span (Replay's "Open in Power") */
  focus?: { t0: number; t1: number; seq: number } | null;
  header?: ReactNode;
  save?: SaveFn;
}

/** The screen without its data source (tests drive it directly). */
export function PowerView({ t, drive, focus, header, save = saveText }: PowerViewProps) {
  const [inputs, setInputs] = useState<PowerInputs>(loadInputs);
  const [draft, setDraft] = useState<Draft>(() => draftOf(loadInputs()));
  const [tab, setTab] = useState<Tab>('curve');
  const [selected, setSelected] = useState<string | null>(null);
  const [compare, setCompare] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
  const [baseline, setBaseline] = useState<Baseline | null>(loadBaseline);
  const [showBaseline, setShowBaseline] = useState(false);
  const [note, setNote] = useState('');

  const analysis = useMemo(() => analyseDrive(drive), [drive]);
  const pulls = useMemo(() => analysis?.pulls ?? [], [analysis]);

  useEffect(() => {
    setCompare(null);
    setPicking(false);
    setSelected(pulls.find((p) => p.quality === 'full')?.id ?? pulls[0]?.id ?? null);
  }, [pulls]);

  useEffect(() => {
    if (!focus || !pulls.length) return;
    const hit = pulls.find((p) => p.pull.t0 <= focus.t1 && p.pull.t1 >= focus.t0);
    if (hit) { setSelected(hit.id); setTab('curve'); }
  }, [focus?.seq, pulls]); // eslint-disable-line react-hooks/exhaustive-deps

  const item = pulls.find((p) => p.id === selected) ?? null;
  const result = useMemo<PowerResult | null>(() => (drive && analysis && item ? computeResult(drive, analysis, item, inputs) : null),
    [drive, analysis, item, inputs]);
  const other = pulls.find((p) => p.id === compare) ?? null;
  const otherResult = useMemo(() => (drive && analysis && other ? computeResult(drive, analysis, other, inputs) : null),
    [drive, analysis, other, inputs]);
  const overlays: Overlay[] = useMemo(() => {
    const out: Overlay[] = [];
    if (otherResult) out.push({ label: `${wallClock(drive, otherResult.item.pull.t0)} pull`, points: otherResult.points, colour: t.series[2] });
    if (showBaseline && baseline) out.push({ label: `baseline ${baseline.label}`, points: baseline.points, colour: t.series[3] });
    return out;
  }, [otherResult, showBaseline, baseline, drive, t]);

  const title = item ? `${wallClock(drive, item.pull.t0)} pull` : '';
  const options = useMemo(() => curveOptions(t, overlays, analysis?.rpmMax ?? null, ''), [t, overlays, analysis]);
  const data = useMemo(() => curveData(result, overlays), [result, overlays]);
  const trace = useMemo(() => (analysis && item ? {
    options: traceOptions(t, item.pull.t1 - item.pull.t0),
    data: traceData(analysis.grid, item.pull.t0, item.pull.t1),
  } : null), [t, analysis, item]);
  const cells = useMemo(() => buildCells(drive), [drive]);

  const commit = (k: keyof PowerInputs, text: string) => {
    const next = { ...draft, [k]: text };
    setDraft(next);
    const parsed = parseDraft(next);
    const err = parsed ? validInputs(parsed) : 'numbers only';
    if (parsed && !err) {
      setInputs(parsed);
      try { localStorage.setItem(INPUTS_KEY, JSON.stringify(parsed)); } catch { /* blocked */ }
      setNote('');
    } else {
      setNote(`Check the inputs: ${err}.`);
    }
  };

  const pick = (p: PullItem) => {
    if (picking) {
      if (p.id !== selected) setCompare(p.id);
      setPicking(false);
      return;
    }
    setSelected(p.id);
    if (compare === p.id) setCompare(null);
  };

  const onCompare = () => {
    if (compare) { setCompare(null); setPicking(false); return; }
    if (!pulls.some((p) => p.id !== selected)) { setNote('No other pull in this drive to compare with.'); return; }
    setPicking(!picking);
  };

  const onBaseline = () => {
    if (!result) return;
    const b = saveBaseline(result, `${drive?.info.name ?? 'drive'} ${title}`);
    setBaseline(b);
    setShowBaseline(false);
    setNote(`Saved as the baseline: ${b.label}.`);
  };

  const onCsv = () => {
    if (!result) return;
    const name = `${drive?.info.name ?? 'drive'}_power_${wallClock(drive, result.item.pull.t0).replace(/[:.\s]/g, '')}`;
    save(`${name}.csv`, 'text/csv', curveCsv(result, inputs, drive?.info.name ?? ''));
    setNote(`Saved ${name}.csv on this device.`);
  };

  const results = result && item ? [
    { label: 'Peak torque', value: `${Math.round(result.peakNm)} N·m`, note: `at ${thousands(result.peakNmRpm)} rpm` },
    { label: 'Power', value: `${Math.round(result.peakHp)} hp`, note: `at ${thousands(result.peakHpRpm)}${result.peakHpAtEnd ? ' (end of pull)' : ' rpm'}` },
    { label: 'vs D16Y8', value: `≈ ${Math.round(result.vsRefPct)} %`, note: item.pull.rpm1 < 5000 ? 'below 5,000 rpm' : 'over the pull' },
    { label: 'Uncertainty', value: `± ${Math.round(result.uncertaintyPct)} %`, note: result.gradeKnown ? 'grade given' : 'grade unknown' },
  ] : [];

  return (
    <div style={{ height: '100%', minHeight: 0, display: 'flex', background: t.bg, color: t.fg, fontFamily: FONTS.sans }}>
      <aside aria-label="Pulls found" style={{ width: 'clamp(210px, 21vw, 290px)', flexShrink: 0, borderRight: `2px solid ${t.rule}`, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <Section t={t}>
          {header}
          <H2>PULLS FOUND</H2>
          <div style={{ fontSize: 12, color: t.fg2, lineHeight: 1.4 }}>Found automatically: one gear, throttle held, rpm rising. Only full-throttle pulls make a curve.</div>
        </Section>
        <div role="listbox" aria-label="Pulls" style={{ overflowY: 'auto', minHeight: 0 }}>
          {pulls.map((p) => {
            const on = p.id === selected, cmp = p.id === compare;
            return (
              <div key={p.id} role="option" aria-selected={on} tabIndex={0} onClick={() => pick(p)}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(p); } }}
                style={{
                  padding: '9px 12px', borderBottom: `1px solid ${t.line}`, display: 'flex', flexDirection: 'column', gap: 2, cursor: 'pointer',
                  background: on ? t.sunk : 'transparent', borderLeft: `5px solid ${on ? t.accent : cmp ? t.series[2] : 'transparent'}`,
                }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <span style={{ fontFamily: FONTS.mono, fontSize: 13, fontWeight: 700 }}>{wallClock(drive, p.pull.t0)}</span>
                  <span style={{ fontSize: 11.5, fontWeight: 800, color: qualityColour(t, p.quality) }}>{QUALITY_LABEL[p.quality]}</span>
                </div>
                <div style={{ fontSize: 12.5, fontWeight: 600 }}>{ordinal(p.pull.gear)} · {thousands(p.pull.rpm0)}–{thousands(p.pull.rpm1)} rpm{cmp ? ' · compared' : ''}</div>
                <div style={{ fontSize: 12, color: t.fg2 }}>{pullDetail(p)}</div>
              </div>
            );
          })}
          {!pulls.length && <div style={{ padding: '10px 12px', fontSize: 12.5, color: t.fg2 }}>{drive ? 'No pulls in this drive.' : 'No drive open.'}</div>}
        </div>
        <div style={{ flexGrow: 1 }} />
        {picking && <div style={{ padding: '8px 12px', background: t.accent, color: t.onAccent, fontSize: 12.5, fontWeight: 700 }}>Pick the pull to compare with.</div>}
        <div style={{ padding: '10px 12px', borderTop: `1px solid ${t.rule}`, fontSize: 12, lineHeight: 1.4, background: t.sunk }}>
          For a full curve: 3rd gear, 2,000 → 6,500 rpm, then the same road the other way.
        </div>
      </aside>

      <section aria-label="Power curve" style={{ flex: '1 1 0', minWidth: 0, display: 'flex', flexDirection: 'column', padding: '10px 14px', gap: 8 }}>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          <Tabs t={t} label="View" value={tab} onChange={setTab} items={[['curve', 'Curve'], ['trace', 'Pull trace'], ['cells', 'Time in cell']]} />
          <div style={{ flexGrow: 1 }} />
          {tab === 'curve' && <>
            <Swatch color={t.series[0]} label="torque" />
            <Swatch color={t.series[1]} label="hp" />
            <Swatch color={t.comment} dashed label="D16Y8 published (approx.)" />
            {overlays.map((o) => <Swatch key={o.label} color={o.colour} label={o.label} />)}
          </>}
        </div>
        <figure style={{ margin: 0, flexGrow: 1, minHeight: 0, border: tab === 'cells' ? undefined : `1px solid ${t.rule}`, position: 'relative' }}>
          {tab === 'curve' && (result
            ? <UPlotChart label={`Torque and horsepower against rpm for the ${title}, with the published D16Y8 curve`} options={options} data={data} />
            : <Empty t={t} title={drive ? 'No curve yet' : 'No drive open'}>{drive
              ? 'This drive has no pull to measure. Log a full-throttle pull in one gear; it shows up on the left.'
              : 'Pick a drive. Drives come from the recorder, the CYD\'s SD card or an imported hondalog.db.'}</Empty>)}
          {tab === 'trace' && (trace
            ? <UPlotChart label={`rpm, speed, throttle and MAP through the ${title}`} options={trace.options} data={trace.data} />
            : <Empty t={t} title="No pull selected" />)}
          {tab === 'cells' && <CellView t={t} cells={cells} title={drive?.info.name ?? 'drive'} />}
        </figure>
        {tab === 'curve' && <div style={{ fontSize: 11.5, color: t.fg2 }}>Measured + modelled. Reference: {REFERENCE_D16Y8.ratedHp} hp @ {thousands(REFERENCE_D16Y8.ratedHpRpm)}, {REFERENCE_D16Y8.ratedNm} N·m @ {thousands(REFERENCE_D16Y8.ratedNmRpm)}; the shape between is approximate. Band = grade, mass, drivetrain and tyre uncertainty.</div>}
      </section>

      <aside aria-label="Result" style={{ width: 'clamp(230px, 22vw, 300px)', flexShrink: 0, borderLeft: `2px solid ${t.rule}`, display: 'flex', flexDirection: 'column', overflowY: 'auto' }}>
        <Section t={t}>
          <H2 style={{ marginBottom: 2 }}>RESULT{title ? ` · ${title.toUpperCase()}` : ''}</H2>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 8 }}>
            {results.map((r) => <ResultCard key={r.label} t={t} {...r} />)}
            {!results.length && <div style={{ gridColumn: '1 / 3', fontSize: 12.5, color: t.fg2 }}>Pick a pull.</div>}
          </div>
        </Section>
        <Section t={t}>
          <H2>INPUTS</H2>
          <Row label="Car + driver"><NumField t={t} label="Car + driver, kg" value={draft.massKg} onChange={(v) => commit('massKg', v)} unit="kg" /></Row>
          <Row label="Tyre"><input aria-label="Tyre size" value={draft.tyre} onChange={(e) => commit('tyre', e.target.value)} style={inputStyle(t, 108)} /></Row>
          <Row label="Gear (rpm ÷ speed)" color={item ? t.ok : t.fg2}>{item ? `${ordinal(item.pull.gear)} ✓` : '---'}</Row>
          <Row label="Road grade" color={inputs.gradePct == null ? t.alarm : t.fg}>
            <NumField t={t} label="Road grade, % (empty = unknown)" value={draft.gradePct} placeholder="unknown" onChange={(v) => commit('gradePct', v)} unit="%" />
          </Row>
          <Row label="Intake air">{result?.iat != null ? `${Math.round(result.iat)} °C` : '---'}</Row>
          <Row label="Baro">{result?.baro != null ? `${result.baro.toFixed(1)} kPa` : '---'}</Row>
          <Row label="Drivetrain loss"><NumField t={t} label="Drivetrain loss, %" value={draft.drivetrainLossPct} onChange={(v) => commit('drivetrainLossPct', v)} unit="%" /></Row>
          <Row label="Road load"><span style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
            <NumField t={t} label="Road load, rolling N" value={draft.rollN} onChange={(v) => commit('rollN', v)} width={52} />
            <span>+</span>
            <NumField t={t} label="Road load, aero N per (m/s)²" value={draft.aeroK} onChange={(v) => commit('aeroK', v)} width={52} />
            <span>v²</span>
          </span></Row>
          <div style={{ fontSize: 11, color: t.fg2 }}>Tyre radius {((tyreRadius(inputs.tyre) ?? 0) * 1000).toFixed(0)} mm. Road load in N, v in m/s.</div>
        </Section>
        <Section t={t} last>
          <Button t={t} primary={!compare} pressed={picking} onClick={onCompare} disabled={!result}>{compare ? 'Stop comparing' : picking ? 'Pick a pull on the left…' : 'Compare with another pull'}</Button>
          <Button t={t} onClick={onBaseline} disabled={!result}>Save as baseline</Button>
          {baseline && <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12.5, fontWeight: 600 }}>
            <input type="checkbox" checked={showBaseline} onChange={(e) => setShowBaseline(e.target.checked)} /> Show baseline ({baseline.label})
          </label>}
          <Button t={t} onClick={onCsv} disabled={!result}>Export CSV</Button>
          <div role="status" style={{ fontSize: 12, color: t.fg2, minHeight: 16 }}>{note}</div>
        </Section>
      </aside>
    </div>
  );
}

/** F5 Power: the drive from the shared drive source (Replay's), the pull from Replay's "Open in Power". */
export default function PowerScreen() {
  const t = useTheme();
  const focus = useReplayNav((s) => s.powerFocus);
  const ds = useDrive(focus?.drive ?? null);
  const header = (
    <div style={{ marginBottom: 6 }}>
      <DriveSelect t={t} drives={ds.drives} value={ds.name} onChange={ds.open} />
      {ds.loading && <div style={{ fontSize: 11.5, color: t.fg2 }}>Loading…</div>}
      {ds.error && <div style={{ fontSize: 11.5, color: t.alarm }}>{ds.error}</div>}
    </div>
  );
  return <PowerView t={t} drive={ds.drive} focus={focus && focus.drive === ds.name ? focus : null} header={header} />;
}
