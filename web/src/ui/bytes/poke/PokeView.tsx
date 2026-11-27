/** The guided poke test (tools/poke_test.py) inside the app: one owner
 *  action at a time while every byte is swept, then the report of which
 *  bytes and bits answered. The live session's polling is paused for the
 *  run and always resumed afterwards. */

import { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Card, downloadFile, mono, Note, Select, TextArea, TextInput, useTheme } from '../controls';
import type { ParsedMap } from '../model/mapdoc';
import { DemoMemLink, type MemoryScanner } from '../model/scanner';
import { analyse, labelsFromMap, renderReport, where, type StepResult } from './analyse';
import { Recorder, runSteps, withLinePaused, type LiveControl, type PokeUi } from './runner';
import { applicable, DEFAULT_STEPS, durationS, ENGINE_OFF_BANNER, parseSteps, scaled, totals, type Step } from './steps';

type Pending =
  | { kind: 'ask'; text: string; choices: string; resolve: (a: string) => void }
  | { kind: 'yesno'; text: string; resolve: (a: boolean) => void }
  | { kind: 'custom'; resolve: (a: { prompt: string; undo: string } | null) => void }
  | { kind: 'timed'; label: string; seconds: number; t0: number; markText?: string; onMark?: () => void; marks: number };

export function PokeView(props: { scanner: MemoryScanner; map: ParsedMap | null; live: LiveControl | null; demo: boolean; clock: () => number }) {
  const t = useTheme();
  const { scanner } = props;
  const [trans, setTrans] = useState<'' | 'auto' | 'manual'>('manual');
  const [optional, setOptional] = useState<'ask' | 'yes' | 'no'>('ask');
  const [scale, setScale] = useState('1');
  const [stepsText, setStepsText] = useState('');
  const [stepsErr, setStepsErr] = useState('');
  const [custom, setCustom] = useState<Step[] | null>(null);
  const [running, setRunning] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const [banner, setBanner] = useState('');
  const [results, setResults] = useState<StepResult[] | null>(null);
  const [report, setReport] = useState('');
  const [error, setError] = useState('');
  const [customPrompt, setCustomPrompt] = useState('');
  const [customUndo, setCustomUndo] = useState('');
  const [, setTick] = useState(0);
  const stop = useRef(false);
  const rec = useRef<Recorder | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const steps = useMemo(() => applicable(custom ?? DEFAULT_STEPS, trans), [custom, trans]);
  const tot = totals(steps);

  useEffect(() => {
    if (pending?.kind !== 'timed') return;
    const id = setInterval(() => setTick((n) => n + 1), 200);
    return () => clearInterval(id);
  }, [pending]);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const ui: PokeUi = {
    ask: (text, choices) => new Promise((resolve) => setPending({ kind: 'ask', text, choices, resolve })),
    yesNo: (text) => new Promise((resolve) => setPending({ kind: 'yesno', text, resolve })),
    custom: () => new Promise((resolve) => { setCustomPrompt(''); setCustomUndo(''); setPending({ kind: 'custom', resolve }); }),
    countdown: (label, seconds, opts) => new Promise((resolve) => {
      setPending({ kind: 'timed', label, seconds, t0: props.clock(), markText: opts?.markText, onMark: opts?.onMark, marks: 0 });
      timer.current = setTimeout(() => { setPending(null); resolve(); }, seconds * 1000);
    }),
    step: (n, total, s, info) => setBanner(`Step ${n}/${total}: ${s.name}${s.optional ? ' (optional)' : ''}${s.engine === 'off' ? ` · ${ENGINE_OFF_BANNER}` : ''}${s.note ? ` · ${s.note}` : ''}${info ? ` · ${info}` : ''}`),
    beep: () => undefined,
  };

  const start = async () => {
    const k = Number(scale) || 1;
    const run = k === 1 ? steps : steps.map((s) => scaled(s, k));
    const r = new Recorder(props.clock);
    rec.current = r;
    stop.current = false;
    setResults(null); setReport(''); setError(''); setRunning(true);
    const off = scanner.onSweep((mem, times) => r.addSweep(mem, times));
    const sim = props.demo && scanner.link instanceof DemoMemLink ? (a: string) => (scanner.link as DemoMemLink).act(a) : undefined;
    const wasSweeping = scanner.sweeping;
    try {
      await withLinePaused(props.live, async () => {
        scanner.setSweeping(true);
        await runSteps(run, r, ui, { trans, optional, sim, setFast: (b) => scanner.setBlocks(b), stopped: () => stop.current });
      });
    } catch (e) {
      setError(`stopped: ${(e as Error).message}`);
    } finally {
      off();
      scanner.setBlocks(null);
      if (!wasSweeping) scanner.setSweeping(false);
      setPending(null);
      setRunning(false);
    }
    const labels = labelsFromMap(props.map);
    const res = analyse(r.sweeps, r.markers(), labels);
    setResults(res);
    setReport(renderReport(res, { start: new Date().toISOString(), port: props.demo ? 'sim:' : 'ECU', mapVersion: props.map?.version, trans, labels }, r.sweeps.length));
  };

  const answer = (a: string) => {
    if (pending?.kind === 'ask') { const p = pending; setPending(null); p.resolve(a); }
  };
  const stopTest = () => {
    stop.current = true;
    if (pending?.kind === 'ask') answer('q');
    else if (pending?.kind === 'yesno') { const p = pending; setPending(null); p.resolve(false); }
    else if (pending?.kind === 'custom') { const p = pending; setPending(null); p.resolve(null); }
  };

  const wideCsv = () => {
    const r = rec.current;
    if (!r) return '';
    const head = ['t', 't_end', 'sweep', ...Array.from({ length: 256 }, (_, i) => `b${i.toString(16).toUpperCase().padStart(2, '0')}`)].join(',');
    return [head, ...r.sweeps.map((sw, i) => [sw.t0.toFixed(3), sw.t1.toFixed(3), i, ...sw.vals.map((v) => (v === null ? '' : v))].join(','))].join('\n') + '\n';
  };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '420px minmax(0, 1fr)', gap: 8, height: '100%', minHeight: 0 }}>
      <Card title="Poke test · steps">
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 8 }}>
          <Select aria-label="gearbox" value={trans} onChange={setTrans} disabled={running}
            options={[['manual', 'Manual gearbox'], ['auto', 'Automatic'], ['', 'Both (all steps)']]} />
          <Select aria-label="optional steps" value={optional} onChange={setOptional} disabled={running}
            options={[['ask', 'Ask for optional steps'], ['yes', 'Do optional steps'], ['no', 'Skip optional steps']]} />
          <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>time ×
            <TextInput aria-label="time scale" value={scale} onChange={setScale} monospace style={{ width: 60 }} disabled={running} /></label>
        </div>
        <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
          <thead><tr style={{ ...mono, textAlign: 'left' }}><th>STEP</th><th>ENGINE</th><th>REPS</th><th>TOTAL</th><th>NOTES</th></tr></thead>
          <tbody>
            {steps.map((s) => (
              <tr key={s.name} style={{ borderBottom: `1px solid ${t.line}` }}>
                <td style={{ ...mono, fontWeight: 700, padding: '4px 2px' }}>{s.name}</td>
                <td style={{ fontWeight: s.engine === 'off' ? 800 : 400, color: s.engine === 'off' ? t.warn : t.fg }}>{s.engine === 'off' ? 'OFF' : 'running'}</td>
                <td style={mono}>{s.repeat}</td><td style={mono}>{durationS(s).toFixed(0)} s</td>
                <td>{[s.optional ? 'optional' : '', s.trans ? `${s.trans} only` : '', s.fast ? `fast ${s.fast}` : '', s.mark ? 'mark' : ''].filter(Boolean).join('; ')}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <Note>Timed total {tot.required.toFixed(0)} s ({(tot.required / 60).toFixed(1)} min) for the required steps, + {tot.optional.toFixed(0)} s if every optional step is done; + {tot.prompts} prompts.</Note>
        <details>
          <summary style={{ cursor: 'pointer', fontWeight: 700 }}>Your own steps (TOML)</summary>
          <TextArea aria-label="steps TOML" value={stepsText} onChange={setStepsText} rows={6} placeholder={'include_defaults = true\n[[step]]\nname = "wipers"\nprompt = "Wipers on."'} />
          <div style={{ display: 'flex', gap: 6, marginTop: 4 }}>
            <Button onClick={() => { try { setCustom(parseSteps(stepsText)); setStepsErr(''); } catch (e) { setStepsErr((e as Error).message); } }} disabled={running}>Use these steps</Button>
            <Button onClick={() => { setCustom(null); setStepsErr(''); }} disabled={running || !custom}>Default steps</Button>
          </div>
          {stepsErr && <Note style={{ color: t.alarm }}>{stepsErr}</Note>}
        </details>
        <Note>Read-only: Honda read requests only, never the expert unlock. {props.live ? 'The live session pauses for the test and resumes after it.' : 'No live session is running.'}{props.demo ? ' Demo RAM: the steps drive the built-in simulator (a dry run).' : ''}</Note>
        <div style={{ display: 'flex', gap: 8 }}>
          <Button kind="primary" onClick={() => void start()} disabled={running || !scanner.available()}>Start the poke test</Button>
          <Button kind="danger" onClick={stopTest} disabled={!running}>Stop</Button>
        </div>
      </Card>
      <Card title={running ? banner || 'Running' : results ? 'Report' : 'Poke test'}>
        {running && pending && (
          <div style={{ border: `2px solid ${t.rule}`, background: t.bg, padding: 14 }} data-testid="poke-prompt">
            {pending.kind === 'ask' && <>
              <div style={{ fontSize: 22, fontWeight: 800, lineHeight: 1.3 }}>{pending.text}</div>
              <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                <Button kind="primary" onClick={() => answer('')}>Done (Enter)</Button>
                {pending.choices.includes('s =') && <Button onClick={() => answer('s')}>Skip step</Button>}
                {pending.choices.includes('q =') && <Button kind="danger" onClick={() => answer('q')}>Stop the test</Button>}
              </div>
            </>}
            {pending.kind === 'yesno' && <>
              <div style={{ fontSize: 20, fontWeight: 800 }}>{pending.text}</div>
              <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                <Button kind="primary" onClick={() => { setPending(null); pending.resolve(true); }}>Yes</Button>
                <Button onClick={() => { setPending(null); pending.resolve(false); }}>No, skip it</Button>
              </div>
            </>}
            {pending.kind === 'custom' && <>
              <div style={{ fontSize: 18, fontWeight: 800 }}>Custom action</div>
              <TextInput aria-label="custom action" value={customPrompt} onChange={setCustomPrompt} placeholder="what will you do? (blank = skip)" style={{ width: '100%', margin: '6px 0' }} />
              <TextInput aria-label="custom undo" value={customUndo} onChange={setCustomUndo} placeholder="how is it undone? (blank = nothing)" style={{ width: '100%', marginBottom: 6 }} />
              <Button kind="primary" onClick={() => { setPending(null); pending.resolve(customPrompt.trim() ? { prompt: customPrompt, undo: customUndo } : null); }}>Go</Button>
            </>}
            {pending.kind === 'timed' && (() => {
              const el = props.clock() - pending.t0;
              const left = Math.max(0, pending.seconds - el);
              return <>
                <div style={{ fontSize: 22, fontWeight: 800 }}>{pending.label}</div>
                <div style={{ ...mono, fontSize: 40, fontWeight: 800 }}>{left.toFixed(0)} s</div>
                <div style={{ height: 14, border: `2px solid ${t.rule}`, position: 'relative' }}>
                  <div style={{ position: 'absolute', inset: 0, width: `${Math.min(100, (el / pending.seconds) * 100)}%`, background: pending.label.startsWith('HOLD') ? t.warn : t.accent }} />
                </div>
                {pending.onMark && <div style={{ marginTop: 10 }}>
                  <Button kind="primary" onClick={() => { pending.onMark!(); setPending({ ...pending, marks: pending.marks + 1 }); }}>Mark: {pending.markText}</Button>
                  {pending.marks > 0 && <span style={{ marginLeft: 8 }}>marked {pending.marks}×</span>}
                </div>}
              </>;
            })()}
          </div>
        )}
        {running && <Note>{rec.current?.sweeps.length ?? 0} sweeps recorded.</Note>}
        {error && <Note style={{ color: t.alarm }}>{error}</Note>}
        {results && (
          <div>
            <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
              <Button onClick={() => downloadFile('report.md', report, 'text/markdown')}>Save report.md</Button>
              <Button onClick={() => downloadFile('findings.json', JSON.stringify(results, null, 1), 'application/json')}>Save findings.json</Button>
              <Button onClick={() => downloadFile('wide.csv', wideCsv(), 'text/csv')}>Save wide.csv</Button>
              <Button onClick={() => downloadFile('markers.json', JSON.stringify(rec.current?.markers() ?? {}, null, 1), 'application/json')}>Save markers.json</Button>
            </div>
            {results.map((r) => (
              <div key={r.name} style={{ borderBottom: `2px solid ${t.sunk}`, padding: '4px 0' }}>
                <strong style={mono}>{r.name}</strong>{r.engine === 'off' ? ' (ENGINE OFF)' : ''} · {r.note || `${r.findings.filter((f) => !f.indirect).length} direct, ${r.findings.filter((f) => f.indirect).length} indirect, ${r.edges.length} edge`}
                {!r.note && r.findings.length > 0 && (
                  <div style={{ ...mono, fontSize: 12 }}>
                    {r.findings.slice(0, 6).map((f, i) => <div key={i}>{where(f)} {f.name ? `(${f.name}) ` : ''}{f.kind} {f.base} → {f.during} {f.indirect ? `[indirect: ${f.indirect}]` : ''}</div>)}
                  </div>
                )}
              </div>
            ))}
            <details style={{ marginTop: 8 }}>
              <summary style={{ cursor: 'pointer', fontWeight: 700 }}>Full report</summary>
              <pre style={{ ...mono, fontSize: 12, whiteSpace: 'pre-wrap' }} data-testid="poke-report">{report}</pre>
            </details>
          </div>
        )}
        {!running && !results && <Note>One owner action at a time, every RAM address 0x00-0xFF swept before, during and after, then a report of which bytes and bits answered. Read the step list first: some steps stop the engine.</Note>}
      </Card>
    </div>
  );
}
