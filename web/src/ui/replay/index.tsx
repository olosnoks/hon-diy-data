/**
 * F4 Replay: a recording played back as if live, everything on one cursor
 * (port of hondalog/ui/screens/replay.py, design canvas "F4 Replay").
 *
 *   transport   play/pause (P), 0.5× / 1× / 4×, ← → step 0.1 s (Shift: 1 s),
 *               [ ] previous / next event, Home / End, a scrubber with event ticks
 *   map         the GPS track coloured by a channel, the position, event pins
 *   lanes       rpm + idle target, speed, throttle, MAP, lateral g; any channel
 *               added by clicking it in the sensor list; cursors A / B (Shift+click,
 *               D drops B at A) with A − B; a second drive in grey (C; A cycles what
 *               to line up on; , . slide it 0.1 s, < > 1 s; Shift+C removes it);
 *               + / − zoom, 0 the whole drive, Ctrl+← → pan
 *   g-g         lateral against longitudinal g with a 5 s trail
 *   readouts    power and torque now, gear, accel, lateral g, grade, fuel flow,
 *               rpm − idle target
 *   sensors     every channel at the cursor, grouped like F1 Live
 * O opens the drive picker.
 */
import { useCallback, useEffect, useRef, useState, type PointerEvent } from 'react';
import type { LoadedDrive } from '../../core/types';
import { ALIGNMENTS, ALIGN_SHORT, ReplayModel } from '../../core/analysis/replay';
import type { AlignHow } from '../../core/analysis/compare';
import { valueAt } from '../../core/analysis/cursors';
import { torqueToHp } from '../../core/analysis/power';
import { useTheme } from '../shell/useTheme';
import { FONTS, type Theme } from '../theme';
import { pullAt, withDerived } from './calc';
import { channelMeta, GROUPS, num, ordinal, valueText } from './channels';
import { DrivePicker } from './DrivePicker';
import { clock, length, signed } from './fmt';
import { GG } from './GG';
import { DEFAULT_LANES, type LaneDef, Lanes } from './Lanes';
import { openInPower, useReplayNav } from './nav';
import { ReplayMap } from './ReplayMap';
import { useDriveSource } from './source';
import { eventsOf, type DriveEvent } from '../events/detect';

export const SPEEDS = [0.5, 1, 4] as const;
export const DEFAULT_WINDOW_S = 60;
export const CALC = ['power', 'torque', 'gear', 'accel_g', 'lat_g', 'grade', 'fuel_lph', 'rpm_err'] as const;

function isTyping(el: EventTarget | null): boolean {
  const e = el as HTMLElement | null;
  if (!e || typeof e.tagName !== 'string') return false;
  return e.tagName === 'INPUT' || e.tagName === 'TEXTAREA' || e.tagName === 'SELECT' || e.isContentEditable;
}

const tickColour = (t: Theme, e: DriveEvent) => ({ stall: t.alarm, near_stall: t.warn, dropout: t.series[2], key_off: t.rule, vtec: t.ok, harsh: t.unverified, mark: t.fg }[e.kind]);

export interface Opened { drive: LoadedDrive; model: ReplayModel; events: DriveEvent[] }

/** Open a drive for replay: calculated channels, events as bookmarks, the first minute showing. */
export function openDrive(raw: LoadedDrive): Opened {
  const drive = withDerived(raw);
  const model = new ReplayModel(drive);
  const events = eventsOf(raw);
  for (const e of events) if (e.kind !== 'mark') model.addEvent(e.t - model.origin, e.kind, e.title);
  const [lo] = model.span;
  model.setWindow(lo, lo + DEFAULT_WINDOW_S);
  return { drive, model, events };
}

export default function ReplayScreen() {
  const t = useTheme();
  const { source, version } = useDriveSource();
  const request = useReplayNav((s) => s.replayRequest);
  const [opened, setOpened] = useState<Opened | null>(null);
  const [status, setStatus] = useState('Loading…');
  const [rev, setRev] = useState(0);
  const bump = useCallback(() => setRev((r) => r + 1), []);
  const [lanes, setLanes] = useState<LaneDef[]>(DEFAULT_LANES);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<number>(1);
  const [picker, setPicker] = useState<null | 'open' | 'compare'>(null);
  const [colourBy, setColourBy] = useState('vss');
  const [note, setNote] = useState('');
  const handled = useRef(0);

  const load = useCallback(async (name: string, at?: number) => {
    try {
      const o = openDrive(await source.loadDrive(name));
      if (at !== undefined) {
        const v = at - o.model.origin;
        o.model.setCursor(v);
        o.model.setWindow(v - 20, v + 10);
      }
      setOpened(o);
      setPlaying(false);
      setNote('');
      setStatus('');
      useReplayNav.setState({ replayDrive: name });
    } catch (e) {
      setStatus(`Cannot open ${name}: ${String(e)}`);
    }
  }, [source]);

  // a request from Events (Open in Replay), else the drive open last, else the newest
  useEffect(() => {
    if (request && request.seq !== handled.current) {
      handled.current = request.seq;
      void load(request.drive, request.t);
      return;
    }
    if (opened) return;
    const last = useReplayNav.getState().replayDrive;
    if (last) { void load(last); return; }
    source.listDrives().then((all) => {
      const d = all.filter((x) => x.kind === 'drive').sort((a, b) => b.startedUnix - a.startedUnix)[0] ?? all[0];
      if (d) void load(d.name);
      else setStatus('No drives recorded yet. Record one (Ctrl+R on the Drive screen), or import a hondalog.db (O).');
    }, (e: unknown) => setStatus(`Cannot list drives: ${String(e)}`));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request, source, version]);

  const model = opened?.model ?? null;

  // ---- actions on the model
  const act = useCallback((f: (m: ReplayModel) => void) => { if (model) { f(model); bump(); } }, [model, bump]);
  const setCursor = useCallback((tt: number, which: 'a' | 'b' = 'a') => act((m) => {
    m.setCursor(tt, which);
    if (which === 'a') m.reveal(m.cursorA);
  }), [act]);
  const stepEvent = useCallback((dir: number) => act((m) => {
    const target = m.jump(dir);
    if (target === null) m.setCursor(dir < 0 ? m.span[0] : m.span[1]);
    m.reveal(m.cursorA);
  }), [act]);
  const togglePlay = useCallback(() => {
    if (!model) return;
    if (!playing && model.cursorA >= model.span[1] - 1e-6) { model.setCursor(model.span[0]); model.reveal(model.span[0]); }
    setPlaying((p) => !p);
    bump();
  }, [model, playing, bump]);

  // ---- playback: the cursor runs at `speed`, the window follows it
  useEffect(() => {
    if (!playing || !model) return;
    const raf = globalThis.requestAnimationFrame ?? ((f: FrameRequestCallback) => setTimeout(() => f(performance.now()), 16) as unknown as number);
    const caf = globalThis.cancelAnimationFrame ?? ((id: number) => clearTimeout(id));
    let last = -1, id = 0;
    const frame = (now: number) => {
      if (last >= 0) {
        const tt = model.setCursor(model.cursorA + ((now - last) / 1000) * speed);
        const [w0, w1] = model.window;
        const w = w1 - w0;
        if (tt > w1 - 0.1 * w || tt < w0) model.setWindow(tt - 0.75 * w, tt + 0.25 * w);
        bump();
        if (tt >= model.span[1] - 1e-6) { setPlaying(false); return; }
      }
      last = now;
      id = raf(frame);
    };
    id = raf(frame);
    return () => caf(id);
  }, [playing, speed, model, bump]);

  // ---- a second drive
  const setOverlay = useCallback(async (name: string | null) => {
    if (!model || !opened) return;
    if (name === null) { model.setOverlay(null); setNote(''); bump(); return; }
    try {
      const other = withDerived(await source.loadDrive(name));
      const want: AlignHow = opened.drive.info.kind !== 'drive' && other.info.kind !== 'drive' ? 'trigger' : 'start';
      const used = model.setOverlay(other, want);
      setNote(used === want ? '' : 'no stall in both drives: lined up on the first sample');
      bump();
    } catch (e) {
      setNote(`cannot open: ${String(e)}`);
    }
  }, [model, opened, source, bump]);
  const alignOverlay = useCallback((how: AlignHow) => act((m) => {
    if (!m.overlayName) return;
    const used = m.alignOverlay(how);
    setNote(used === how ? '' : `no ${ALIGNMENTS.find(([k]) => k === how)?.[1]} in both drives: lined up on the first sample`);
  }), [act]);
  const cycleAlign = useCallback(() => {
    if (!model?.overlayName) return;
    const keys = ALIGNMENTS.map(([k]) => k);
    const i = keys.indexOf(model.align as AlignHow);
    alignOverlay(keys[(i + 1) % keys.length]!);
  }, [model, alignOverlay]);

  const addLane = useCallback((ch: string) => setLanes((ls) => (ls.some((l) => l.id === ch) ? ls : [...ls, { id: ch }])), []);

  // ---- keys
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (picker || isTyping(e.target) || e.altKey || e.metaKey || !model) return;
      const k = e.key;
      let used = true;
      if ((k === 'ArrowLeft' || k === 'ArrowRight') && e.ctrlKey) act((m) => m.pan(k === 'ArrowLeft' ? -0.25 : 0.25));
      else if (e.ctrlKey) used = false;
      else if (k === 'ArrowLeft' || k === 'ArrowRight') setCursor(model.cursorA + (k === 'ArrowLeft' ? -1 : 1) * (e.shiftKey ? 1 : 0.1));
      else if (k === 'Home') setCursor(model.span[0]);
      else if (k === 'End') setCursor(model.span[1]);
      else if (k === '[') stepEvent(-1);
      else if (k === ']') stepEvent(1);
      else if (k === 'p' || k === 'P') togglePlay();
      else if (k === '+' || k === '=') act((m) => m.zoom(0.5, m.cursorA));
      else if (k === '-' || k === '_') act((m) => m.zoom(2, m.cursorA));
      else if (k === '0') act((m) => m.showAll());
      else if (k === 'o' || k === 'O') setPicker('open');
      else if (k === 'd' || k === 'D') act((m) => (m.cursorB === null ? m.setCursor(m.cursorA, 'b') : m.clearCursorB()));
      else if (k === 'C') void setOverlay(null);
      else if (k === 'c') { if (model.overlayName) cycleAlign(); else setPicker('compare'); }
      else if (k === 'a' || k === 'A') cycleAlign();
      else if (k === ',' || k === '<') act((m) => m.nudgeOverlay(k === '<' ? -1 : -0.1));
      else if (k === '.' || k === '>') act((m) => m.nudgeOverlay(k === '>' ? 1 : 0.1));
      else used = false;
      if (used) e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [picker, model, act, setCursor, stepEvent, togglePlay, setOverlay, cycleAlign]);

  const pickerEl = picker && (
    <DrivePicker source={source} theme={t} title={picker === 'open' ? 'Open a drive' : 'Lay a second drive over this one'}
      exclude={picker === 'compare' ? opened?.drive.info.name : undefined}
      onClose={() => setPicker(null)}
      onOpen={(name) => { const which = picker; setPicker(null); if (which === 'open') void load(name); else void setOverlay(name); }} />
  );

  if (!opened || !model) {
    return (
      <div style={{ position: 'relative', flexGrow: 1, background: t.bg, color: t.fg, fontFamily: FONTS.sans, padding: 24, display: 'flex', flexDirection: 'column', gap: 12, alignItems: 'flex-start' }}>
        <div style={{ fontSize: 15, fontWeight: 600, color: t.fg2 }}>{status}</div>
        <button type="button" onClick={() => setPicker('open')} style={btnStyle(t, true)}>Open a drive (O)</button>
        {pickerEl}
      </div>
    );
  }

  void rev;   // the model is mutable: rev re-renders after each change
  const { drive, events } = opened;
  const origin = model.origin;
  const unix0 = drive.info.startedUnix + origin;
  const tA = model.cursorA, tB = model.cursorB;
  const now = tA + origin;                        // drive time at cursor A
  const [s0, s1] = model.span;
  const frac = (x: number) => Math.min(1, Math.max(0, (x - s0) / ((s1 - s0) || 1)));
  const at = (ch: string) => (drive.series[ch] ? valueAt(drive.series[ch]!, now) : NaN);
  const overlayKey = `${model.overlayName}@${model.overlayOffset}@${model.align}`;

  const scrub = (e: PointerEvent<HTMLDivElement>) => {
    if (e.type === 'pointermove' && !(e.buttons & 1)) return;
    const r = e.currentTarget.getBoundingClientRect();
    if (r.width > 0) setCursor(s0 + ((e.clientX - r.left) / r.width) * (s1 - s0));
  };

  const pull = pullAt(drive.series, now);
  const rpmNow = at('rpm'), torque = at('torque');

  return (
    <div style={{ position: 'relative', flexGrow: 1, minHeight: 0, display: 'flex', flexDirection: 'column', background: t.bg, color: t.fg, fontFamily: FONTS.sans }}>
      {/* transport */}
      <div style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 10, padding: '6px 10px', borderBottom: `1px solid ${t.line}` }}>
        <button type="button" aria-label={playing ? 'Pause' : 'Play'} onClick={togglePlay}
          style={{ width: 40, height: 34, background: t.accent, color: t.onAccent, border: 0, fontSize: 15, fontWeight: 800, cursor: 'pointer' }}>
          {playing ? '❚❚' : '▶'}
        </button>
        <div role="group" aria-label="Speed" style={{ display: 'flex', gap: 4 }}>
          {SPEEDS.map((s) => (
            <button key={s} type="button" aria-pressed={speed === s} onClick={() => setSpeed(s)}
              style={{ fontSize: 12, fontWeight: 700, padding: '6px 9px', cursor: 'pointer', fontFamily: FONTS.sans,
                background: speed === s ? t.accent : 'transparent', color: speed === s ? t.onAccent : t.fg, border: `1px solid ${speed === s ? t.accent : t.line}` }}>
              {s}×
            </button>
          ))}
        </div>
        <div style={{ marginLeft: 6, display: 'flex', flexDirection: 'column', lineHeight: 1.1 }}>
          <span data-testid="clock" style={{ fontFamily: FONTS.mono, fontSize: 16, fontWeight: 700 }}>{tB !== null ? 'A ' : ''}{clock(unix0 + tA, 1)}</span>
          {tB !== null && <span data-testid="delta" style={{ fontFamily: FONTS.mono, fontSize: 12, fontWeight: 700, color: t.accent }}>A−B {signed(tA - tB, 2)} s</span>}
        </div>
        <div data-testid="scrubber" role="slider" aria-label="Scrub" aria-valuemin={0} aria-valuemax={Math.round(s1 - s0)} aria-valuenow={Math.round(tA - s0)}
          onPointerDown={(e) => { e.currentTarget.setPointerCapture?.(e.pointerId); scrub(e); }} onPointerMove={scrub}
          style={{ flexGrow: 1, position: 'relative', height: 30, margin: '0 8px', cursor: 'pointer', minWidth: 80 }}>
          <div style={{ position: 'absolute', left: 0, right: 0, top: 13, height: 4, background: t.line }} />
          <div style={{ position: 'absolute', left: 0, width: `${frac(tA) * 100}%`, top: 13, height: 4, background: t.rule }} />
          {events.map((e) => {
            const h = e.kind === 'near_stall' || e.kind === 'stall' ? 22 : 18;
            return <div key={e.id} data-tick={e.kind} title={`${clock(e.unix)} ${e.title}`}
              style={{ position: 'absolute', left: `${frac(e.t - origin) * 100}%`, top: 15 - h / 2, width: 4, height: h, background: tickColour(t, e) }} />;
          })}
          <div style={{ position: 'absolute', left: `${frac(tA) * 100}%`, top: 0, bottom: 0, width: 3, background: t.livePoint }} />
        </div>
        <span style={{ fontSize: 12, fontWeight: 600, color: t.fg2, whiteSpace: 'nowrap' }}>◀ ▶ step 0.1 s · [ ] prev/next event</span>
        <button type="button" onClick={() => setPicker('open')} style={btnStyle(t)}>Open (O)</button>
        <button type="button" onClick={() => (model.overlayName ? void setOverlay(null) : setPicker('compare'))} style={btnStyle(t)}>
          {model.overlayName ? 'Remove compare' : 'Compare (C)'}
        </button>
      </div>

      {/* what is open, the second drive */}
      <div style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 10, padding: '3px 10px', fontSize: 12.5, fontWeight: 600, color: t.fg2, minHeight: 22 }}>
        <span data-testid="drive-name" style={{ fontFamily: FONTS.mono, color: t.fg, fontWeight: 700 }}>{drive.info.name}</span>
        <span>{length(drive.info.durationS)}</span>
        {model.overlayName && (
          <>
            <span style={{ width: 10, height: 10, background: t.rule, display: 'inline-block' }} />
            <span data-testid="overlay-note">{model.overlayName} {signed(model.overlayOffset, 2)} s on {ALIGN_SHORT[model.align as AlignHow] ?? model.align}</span>
            <select aria-label="Line the second drive up on" value={model.align} onChange={(e) => alignOverlay(e.target.value as AlignHow)}
              style={{ background: t.bg, color: t.fg, border: `1px solid ${t.rule}`, fontSize: 12, fontFamily: FONTS.sans }}>
              {ALIGNMENTS.map(([k, label]) => <option key={k} value={k}>line up on {label}</option>)}
            </select>
            <button type="button" aria-label="Slide it 0.1 s earlier" onClick={() => act((m) => m.nudgeOverlay(-0.1))} style={btnStyle(t, false, true)}>‹ 0.1 s</button>
            <button type="button" aria-label="Slide it 0.1 s later" onClick={() => act((m) => m.nudgeOverlay(0.1))} style={btnStyle(t, false, true)}>0.1 s ›</button>
          </>
        )}
        {note && <span style={{ color: t.warn }}>{note}</span>}
      </div>

      <div style={{ flexGrow: 1, minHeight: 0, display: 'flex', gap: 8, padding: 8, paddingTop: 2 }}>
        {/* map, g-g, this pull */}
        <div style={{ width: 'clamp(220px, 29vw, 400px)', flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 8, minHeight: 0 }}>
          <figure style={{ margin: 0, flex: '1 1 0', minHeight: 0, border: `1px solid ${t.line}`, background: t.panel, position: 'relative' }}>
            <ReplayMap drive={drive} theme={t} cursorT={now} events={events} colourBy={colourBy} onColourBy={setColourBy}
              onPick={(tt) => setCursor(tt - origin)} />
          </figure>
          <div style={{ height: 'clamp(140px, 26vh, 200px)', flexShrink: 0, display: 'flex', gap: 8 }}>
            <figure style={{ margin: 0, aspectRatio: '1', height: '100%', border: `1px solid ${t.line}`, background: t.panel }}>
              <GG drive={drive} t={now} theme={t} />
            </figure>
            <div style={{ flex: '1 1 0', minWidth: 0, border: `1px solid ${t.line}`, padding: '8px 10px', display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ fontSize: 12, fontWeight: 800, letterSpacing: '0.06em' }}>THIS PULL</div>
              <div data-testid="pull" style={{ fontSize: 12.5, color: t.fg2 }}>
                {pull
                  ? `${Number.isFinite(pull.gear) ? `${ordinal(pull.gear)} gear · ` : ''}${num(pull.rpm0)} → ${num(pull.rpm1)} rpm · ${pull.tpsMean > 85 ? 'full' : 'part'} throttle ${num(pull.tpsMean)} %`
                  : 'Not on the throttle here.'}
              </div>
              <div style={{ fontFamily: FONTS.mono, fontSize: 18, fontWeight: 700 }}>
                {Number.isFinite(torque) ? `${num(torque)} N·m · ${num(torqueToHp(torque, rpmNow))} hp` : '—'}
              </div>
              <div style={{ fontSize: 12, color: t.fg2 }}>{Number.isFinite(rpmNow) ? `at ${num(rpmNow)} rpm, now (modelled)` : ''}</div>
              <div style={{ flexGrow: 1 }} />
              <button type="button" onClick={() => openInPower(drive.info.name, pull?.t0 ?? now - 5, pull?.t1 ?? now + 5)}
                style={{ ...btnStyle(t), minHeight: 32 }}>Open in Power (F5)</button>
            </div>
          </div>
        </div>

        {/* lanes and calculated readouts */}
        <div style={{ flex: '1 1 0', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <figure style={{ margin: 0, flex: '1 1 0', minHeight: 0, border: `1px solid ${t.line}`, background: t.panel }}>
            <Lanes model={model} drive={drive} lanes={lanes} events={events} theme={t} window={model.window}
              cursorA={tA} cursorB={tB} overlayKey={overlayKey}
              onCursor={(tt, which) => setCursor(tt, which)} onZoom={(f, around) => act((m) => m.zoom(f, around))}
              onPan={(f) => act((m) => m.pan(f))} onRemove={(id) => setLanes((ls) => ls.filter((l) => l.id !== id))} />
          </figure>
          <div data-testid="calc" style={{ flexShrink: 0, display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 6 }}>
            {CALC.map((ch) => {
              const meta = channelMeta(ch, drive);
              return (
                <button key={ch} type="button" title="Click to add as a lane" onClick={() => addLane(ch)}
                  style={{ border: `1px solid ${t.line}`, padding: '5px 8px', background: t.panel, textAlign: 'left', cursor: 'pointer', fontFamily: FONTS.sans, color: t.fg }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: t.fg2 }}>{meta.label}</div>
                  <div data-calc={ch} style={{ fontFamily: FONTS.mono, fontSize: 17, fontWeight: 700 }}>{valueText(meta, at(ch))}</div>
                </button>
              );
            })}
          </div>
        </div>

        {/* every sensor at the cursor */}
        <aside style={{ width: 'clamp(200px, 24vw, 330px)', flexShrink: 0, border: `1px solid ${t.line}`, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div style={{ padding: '6px 10px', borderBottom: `1px solid ${t.line}`, display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
            <span style={{ fontSize: 12, fontWeight: 800, letterSpacing: '0.06em' }}>ALL SENSORS AT CURSOR</span>
            <span style={{ fontSize: 11, color: t.fg2 }}>click = add lane</span>
          </div>
          <div style={{ flexGrow: 1, minHeight: 0, overflowY: 'auto', padding: '4px 10px', display: 'flex', flexDirection: 'column', gap: 6 }}>
            {GROUPS.filter((g) => g.key !== 'calc').map((g) => {
              const rows = Object.keys(drive.series).filter((ch) => channelMeta(ch, drive).group === g.key && drive.series[ch]!.t.length);
              if (!rows.length) return null;
              return (
                <div key={g.key} data-group={g.key}>
                  <div style={{ fontSize: 11, fontWeight: 800, letterSpacing: '0.06em', color: t.accent, padding: '3px 0' }}>{g.title}</div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', columnGap: 12, rowGap: 1 }}>
                    {rows.map((ch) => {
                      const meta = channelMeta(ch, drive);
                      const on = lanes.some((l) => l.id === ch || l.extra === ch);
                      return (
                        <button key={ch} type="button" data-sensor={ch} onClick={() => addLane(ch)} aria-pressed={on}
                          style={{ display: 'flex', justifyContent: 'space-between', gap: 6, fontSize: 12, border: 0, borderBottom: `1px solid ${t.sunk}`,
                            padding: '2px 0', background: 'transparent', color: t.fg, cursor: 'pointer', fontFamily: FONTS.sans, textAlign: 'left' }}>
                          <span style={{ color: on ? t.accent : t.fg2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{meta.label}</span>
                          <span style={{ fontFamily: FONTS.mono, fontWeight: 700, whiteSpace: 'nowrap' }}>{valueText(meta, at(ch))}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </aside>
      </div>
      {pickerEl}
    </div>
  );
}

function btnStyle(t: Theme, primary = false, small = false) {
  return {
    fontFamily: FONTS.sans, fontSize: small ? 12 : 12.5, fontWeight: 700, minHeight: small ? 24 : 30, padding: small ? '0 8px' : '0 12px', cursor: 'pointer',
    background: primary ? t.accent : t.bg, color: primary ? t.onAccent : t.fg, border: `2px solid ${primary ? t.accent : t.rule}`, whiteSpace: 'nowrap' as const,
  };
}
