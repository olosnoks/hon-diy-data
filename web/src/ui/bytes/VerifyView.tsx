/** Verify: "Map a channel · guided" (the desktop's wizard, MapWizardCard),
 *  and the verification scripts (mapping/verify.py) run on the live sweeps:
 *  phases with instructions and minimum times, typed references, live
 *  verdicts, then confirm / reject per channel. */

import { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Card, Check, mono, Note, StatusMark, TextInput, useTheme } from './controls';
import { MapWizardCard } from './MapWizardCard';
import type { ParsedMap } from './model/mapdoc';
import type { MemoryScanner } from './model/scanner';
import {
  coverage, FAIL, PASS, PLAUSIBLE, resultLine, SCRIPTS, scriptChannels, SKIP, VerificationSession, type ChannelVerdict, type Verdict,
} from './model/verify';

const SOURCES: Record<string, string> = { batt_meter: 'multimeter', tach_rpm: 'tachometer', speedo_kmh: 'speedometer', ambient_c: 'air temperature' };

function VerdictMark({ status }: { status: Verdict }) {
  const t = useTheme();
  const style = { ...mono, fontSize: 12, fontWeight: 700, padding: '1px 6px' } as const;
  if (status === PASS) return <span style={{ ...style, background: t.ok, color: t.bg }}>PASS</span>;
  if (status === FAIL) return <span style={{ ...style, background: t.alarm, color: t.onAlarm }}>FAIL</span>;
  if (status === PLAUSIBLE) return <span style={{ ...style, border: `2px dashed ${t.unverified}`, color: t.unverified }}>PLAUSIBLE</span>;
  return <span style={{ ...style, border: `2px solid ${t.comment}`, color: t.fg2 }}>SKIP</span>;
}

type VerifyProps = { scanner: MemoryScanner; map: ParsedMap | null; mapText: string; setMapText: (t: string) => void; clock: () => number };
export const VERIFY_MODES = [['guided', 'Map a channel · guided'], ['scripts', 'Scripts']] as const;

export function VerifyView(props: VerifyProps) {
  const t = useTheme();
  const [mode, setMode] = useState<'guided' | 'scripts'>('guided');
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, height: '100%', minHeight: 0 }}>
      <div role="group" aria-label="Verify mode" style={{ display: 'flex', flexShrink: 0 }}>
        {VERIFY_MODES.map(([k, label]) => (
          <button key={k} type="button" aria-pressed={mode === k} onClick={() => setMode(k)}
            style={{ background: mode === k ? t.accent : 'transparent', color: mode === k ? t.onAccent : t.fg, border: `2px solid ${mode === k ? t.accent : t.line}`,
              padding: '5px 12px', fontWeight: 700, fontSize: 13, cursor: 'pointer', borderRadius: 0, marginRight: -2 }}>
            {label}
          </button>
        ))}
      </div>
      <div style={{ flex: '1 1 0', minHeight: 0 }}>
        {mode === 'guided' ? <MapWizardCard {...props} /> : <ScriptsView {...props} />}
      </div>
    </div>
  );
}

function ScriptsView(props: VerifyProps) {
  const t = useTheme();
  const { scanner, map } = props;
  const [scriptId, setScriptId] = useState('koeo');
  const [refs, setRefs] = useState<Record<string, string>>({});
  const [answers, setAnswers] = useState<Record<string, boolean>>({});
  const session = useRef<VerificationSession | null>(null);
  const [, setTick] = useState(0);
  const [msg, setMsg] = useState('');
  const [force, setForce] = useState(false);
  const script = SCRIPTS[scriptId]!;
  const cov = useMemo(() => (map ? coverage(map) : []), [map]);
  const s = session.current;

  useEffect(() => {
    const off = scanner.onSweep((mem) => { session.current?.addSweep(mem); });
    const timer = setInterval(() => setTick((n) => n + 1), 250);
    return () => { off(); clearInterval(timer); };
  }, [scanner]);

  const start = () => {
    if (!map) return;
    const ses = new VerificationSession(script, map, props.clock);
    for (const r of script.references) {
      const v = refs[r.name] ?? (r.default !== undefined ? String(r.default) : '');
      if (v.trim() && Number.isFinite(Number(v))) ses.setReference(r.name, Number(v), SOURCES[r.name] ?? 'typed reading');
    }
    for (const q of script.questions) ses.answer(q.name, !!answers[q.name]);
    ses.start();
    session.current = ses;
    if (!scanner.sweeping) scanner.setSweeping(true);
    setMsg('');
    setTick((n) => n + 1);
  };

  const verdicts: Record<string, ChannelVerdict> = s ? (s.report?.verdicts ?? (s.state === 'running' ? s.evaluate() : {})) : {};
  const act = (fn: () => string, done: string) => {
    try { props.setMapText(fn()); setMsg(done); } catch (e) { setMsg((e as Error).message); }
  };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '340px minmax(0, 1fr)', gap: 8, height: '100%', minHeight: 0 }}>
      <Card title="Scripts">
        {Object.values(SCRIPTS).map((sc) => (
          <button key={sc.id} onClick={() => { if (!s || s.state !== 'running') { setScriptId(sc.id); session.current = null; } }}
            aria-pressed={sc.id === scriptId}
            style={{ display: 'block', width: '100%', textAlign: 'left', background: sc.id === scriptId ? t.fg : 'transparent', color: sc.id === scriptId ? t.bg : t.fg,
              border: 'none', borderBottom: `2px solid ${t.sunk}`, padding: '8px 6px', cursor: 'pointer', borderRadius: 0 }}>
            <div style={{ fontWeight: 800 }}>{sc.title}</div>
            <div style={{ ...mono, fontSize: 12 }}>{scriptChannels(sc).join(' ')}</div>
          </button>
        ))}
        <h4 style={{ margin: '12px 0 4px' }}>Coverage</h4>
        <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
          <tbody>
            {cov.map((c) => (
              <tr key={c.channel} style={{ borderBottom: `1px solid ${t.line}` }}>
                <td style={{ ...mono, fontWeight: 700 }}>{c.channel}</td><td><StatusMark status={c.status} /></td>
                <td style={mono}>{c.strong.join(' ') || (c.weak.length ? `(${c.weak.join(' ')})` : 'finder only')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card title={script.title} right={s?.state === 'running' ? <span style={mono}>{s.phaseElapsed.toFixed(0)} s</span> : null}>
        <Note style={{ color: t.fg }}>{script.intro}</Note>
        {!map && <Note style={{ color: t.alarm }}>The address map does not load: fix it on the Address map tab first.</Note>}
        {(!s || s.state === 'ready') && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxWidth: 560 }}>
            {script.references.map((r) => (
              <label key={r.name} style={{ display: 'grid', gridTemplateColumns: '1fr 120px 50px', gap: 8, alignItems: 'center' }}>
                {r.prompt}
                <TextInput aria-label={r.name} value={refs[r.name] ?? (r.default !== undefined ? String(r.default) : '')}
                  onChange={(v) => setRefs({ ...refs, [r.name]: v })} monospace placeholder="leave empty" />
                <span>{r.units}</span>
              </label>
            ))}
            {script.questions.map((q) => (
              <Check key={q.name} checked={!!answers[q.name]} onChange={(v) => setAnswers({ ...answers, [q.name]: v })}>{q.prompt}</Check>
            ))}
            <div><Button kind="primary" onClick={start} disabled={!map || !scanner.available()}>Start</Button></div>
            {!scanner.available() && <Note>Connect first: the checks run on the memory sweep.</Note>}
          </div>
        )}
        {s?.state === 'running' && s.phase && (
          <div style={{ border: `2px solid ${t.rule}`, background: t.bg, padding: 12, margin: '8px 0' }}>
            <div style={{ ...mono, fontSize: 12 }}>phase {s.phaseIndex + 1} of {script.phases.length}</div>
            <div style={{ fontSize: 22, fontWeight: 800 }} data-testid="verify-phase">{s.phase.title}</div>
            <div style={{ fontSize: 15, fontWeight: 600, margin: '6px 0' }}>{s.phase.instruction}</div>
            <div style={{ height: 12, border: `2px solid ${t.rule}`, position: 'relative', margin: '8px 0' }}>
              <div style={{ position: 'absolute', inset: 0, width: `${Math.min(100, (s.phaseElapsed / s.phase.minS) * 100)}%`, background: t.accent }} />
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <Button kind="primary" disabled={!s.canAdvance} onClick={() => { if (!s.nextPhase()) s.finish(); setTick((n) => n + 1); }}>
                {s.phaseIndex + 1 < script.phases.length ? 'Next phase' : 'Finish'}
              </Button>
              <Button onClick={() => { s.finish(); setTick((n) => n + 1); }}>Stop early</Button>
              <span style={{ ...mono, alignSelf: 'center' }}>{s.recording.length} sweeps</span>
            </div>
          </div>
        )}
        {s && s.state !== 'ready' && (
          <div>
            <h4 style={{ margin: '8px 0 4px' }}>{s.state === 'done' ? 'Result' : 'So far'}</h4>
            {Object.values(verdicts).map((v) => (
              <div key={v.channel} style={{ borderBottom: `2px solid ${t.sunk}`, padding: '6px 0' }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <strong style={{ ...mono, minWidth: 110 }}>{v.channel}</strong><VerdictMark status={v.status} />
                  {s.state === 'done' && v.status !== SKIP && map?.channels.some((c) => c.name === v.channel) && (
                    <span style={{ display: 'flex', gap: 6, marginLeft: 'auto' }}>
                      <Button disabled={v.status === FAIL || (v.status === PLAUSIBLE && !force)}
                        onClick={() => act(() => s.confirmEdit(v.channel, props.mapText, '', force), `${v.channel} marked verified in the map (save it on the Address map tab)`)}>
                        Mark verified
                      </Button>
                      <Button onClick={() => act(() => s.rejectEdit(v.channel, props.mapText), `${v.channel} marked suspect in the map (save it on the Address map tab)`)}>Mark suspect</Button>
                    </span>
                  )}
                </div>
                <ul style={{ ...mono, fontSize: 12, margin: '4px 0', paddingLeft: 18 }}>
                  {v.results.map((r, i) => <li key={i}>{resultLine(r)}</li>)}
                </ul>
                {s.state === 'done' && v.status === FAIL && (() => {
                  const sug = s.suggest(v.channel);
                  return sug.length ? <Note>Where it might live instead: {sug.join(', ')}. Point the channel there on the Address map tab or with Rank, then run the script again.</Note> : null;
                })()}
              </div>
            ))}
            {s.state === 'done' && (
              <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 8 }}>
                <Check checked={force} onChange={setForce}>Accept plausible-only (weak checks) as verified</Check>
                <Button onClick={() => { session.current = null; setTick((n) => n + 1); }}>New run</Button>
              </div>
            )}
            {msg && <Note style={{ color: t.fg }}>{msg}</Note>}
            <Note>A pass is a recommendation; marking is your call and records what was measured as evidence.</Note>
          </div>
        )}
      </Card>
    </div>
  );
}
