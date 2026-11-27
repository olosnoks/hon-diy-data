/**
 * "Map a channel · guided" on the Verify page (the desktop's WizardCard,
 * screens/rawtools.py): the step row, the one emphasised instruction box, the
 * channel list (step 0) or the candidate addresses (step 3), and Back (B) /
 * Not it, try again / the primary button (N). The steps are model/wizard.ts.
 */
import { useEffect, useMemo, useState } from 'react';
import { Button, Card, mono, useTheme } from './controls';
import type { ParsedMap } from './model/mapdoc';
import type { MemoryScanner } from './model/scanner';
import { MapWizard } from './model/wizard';

const hex2 = (v: number) => (v >= 0 ? v.toString(16).toUpperCase().padStart(2, '0') : '--');

export function MapWizardCard(props: { scanner: MemoryScanner; map: ParsedMap | null; mapText: string; setMapText: (t: string) => void; clock: () => number }) {
  const t = useTheme();
  const { scanner, map } = props;
  const wizard = useMemo(() => new MapWizard(scanner, props.clock), [scanner, props.clock]);
  const [, setTick] = useState(0);
  const bump = () => setTick((n) => n + 1);

  useEffect(() => {
    const off = scanner.onSweep((mem) => wizard.addSweep(mem));
    const timer = setInterval(() => { wizard.tick(); bump(); }, 250);
    return () => { off(); clearInterval(timer); };
  }, [scanner, wizard]);

  const next = () => {
    const text = wizard.next(map, props.mapText);
    if (text !== null) props.setMapText(text);
    bump();
  };
  const back = () => { wizard.back(); bump(); };
  const retry = () => { wizard.retry(map); bump(); };

  // N next, B back (desktop RawScreen.key), not while typing
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT')) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === 'n' || e.key === 'N') { next(); e.preventDefault(); }
      else if (e.key === 'b' || e.key === 'B') { back(); e.preventDefault(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  const w = wizard.view(map);
  const sel = wizard.picked ?? w.candidates[0]?.addr ?? null;
  const primaryOff = (w.step === 0 && w.primary !== 'Next') || w.primary === 'Recording…';
  const row = { display: 'grid', alignItems: 'center', minHeight: 40, borderBottom: `2px solid ${t.sunk}`, cursor: 'pointer', padding: '0 8px' } as const;

  return (
    <Card title="Map a channel · guided" right={w.target ? <span data-testid="wizard-target" style={{ fontSize: 14, fontWeight: 700 }}>Target: {w.target}</span> : null}
      style={{ height: '100%' }} bodyStyle={{ display: 'flex', flexDirection: 'column', gap: 8, overflow: 'hidden' }}>
      <ol aria-label="Steps" style={{ display: 'grid', gridTemplateColumns: `repeat(${w.steps.length}, minmax(0, 1fr))`, gap: 6, margin: 0, padding: 0, listStyle: 'none', flexShrink: 0 }}>
        {w.steps.map((name, i) => {
          const done = i < w.step, on = i === w.step;
          return (
            <li key={name} aria-current={on ? 'step' : undefined} style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
              <span style={{ ...mono, width: 26, height: 26, flexShrink: 0, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, fontWeight: 700,
                background: done ? t.ok : on ? t.accent : t.panel, color: done ? t.panel : on ? t.onAccent : t.fg, border: `3px solid ${done ? t.ok : on ? t.accent : t.rule}`, boxSizing: 'border-box' }}>
                {done ? '✓' : i + 1}
              </span>
              <span style={{ fontSize: 13, fontWeight: 700, color: done || on ? t.fg : t.fg2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name}</span>
            </li>
          );
        })}
      </ol>
      <div data-testid="wizard-box" style={{ border: `4px solid ${t.rule}`, background: t.panel, padding: '8px 14px', flexShrink: 0, position: 'relative' }}>
        <div data-testid="wizard-title" style={{ fontSize: 22, fontWeight: 800, paddingRight: w.busy !== null ? 180 : 0 }}>{w.title}</div>
        <div style={{ fontSize: 15, fontWeight: 600, marginTop: 4, lineHeight: 1.35, maxHeight: 62, overflow: 'hidden' }}>{w.body}</div>
        {w.busy !== null && (
          <div aria-label="Progress" style={{ position: 'absolute', right: 14, top: 14, width: 160, height: 14, border: `2px solid ${t.rule}`, boxSizing: 'border-box' }}>
            <div style={{ position: 'absolute', inset: 0, width: `${Math.round(w.busy * 100)}%`, background: t.accent }} />
          </div>
        )}
      </div>
      <div role="listbox" aria-label={w.column} style={{ flex: '1 1 0', minHeight: 0, overflowY: 'auto' }}>
        {w.step === 0 && (
          <>
            <div style={{ ...mono, fontSize: 12, fontWeight: 700, padding: '0 8px 4px' }}>{w.column}</div>
            {w.targets.map((text, i) => (
              <div key={text} role="option" aria-selected={i === w.targetIndex} onClick={() => { wizard.pickTarget(i); bump(); }}
                style={{ ...row, gridTemplateColumns: '1fr', background: i === w.targetIndex ? t.sunk : 'transparent', borderLeft: `5px solid ${i === w.targetIndex ? t.accent : 'transparent'}` }}>
                <span style={{ ...mono, fontSize: 14, fontWeight: 700, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{text}</span>
              </div>
            ))}
            {!w.targets.length && <div style={{ padding: 8, color: t.fg2 }}>The address map does not load: fix it on the Address map tab first.</div>}
          </>
        )}
        {w.step === 3 && w.candidates.length > 0 && (
          <>
            <div style={{ ...row, cursor: 'default', minHeight: 22, gridTemplateColumns: '80px minmax(0, 1fr) 110px 50px', ...mono, fontSize: 12, fontWeight: 700 }}>
              <span>ADDR</span><span>{w.column}</span><span>RANGE SEEN</span><span style={{ textAlign: 'right' }}>NOW</span>
            </div>
            {w.candidates.map((c) => (
              <div key={c.addr} role="option" aria-selected={c.addr === sel} data-addr={c.addr} onClick={() => { wizard.pickCandidate(c.addr); bump(); }}
                style={{ ...row, gridTemplateColumns: '80px minmax(0, 1fr) 110px 50px', background: c.addr === sel ? t.sunk : 'transparent', borderLeft: `5px solid ${c.addr === sel ? t.accent : 'transparent'}` }}>
                <span style={{ ...mono, fontSize: 15, fontWeight: 700 }}>0x{hex2(c.addr)}</span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                  <span style={{ flex: '0 1 210px', height: 12, border: `2px solid ${t.fg}`, position: 'relative', boxSizing: 'border-box' }}>
                    <span style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${Math.round(Math.min(1, Math.max(0, c.score)) * 100)}%`, background: t.fg }} />
                  </span>
                  <span style={{ ...mono, fontSize: 14, fontWeight: 700 }}>{c.score.toFixed(2)}</span>
                </span>
                <span style={{ ...mono, fontSize: 14, fontWeight: 700 }}>{c.lo}–{c.hi}</span>
                <span style={{ ...mono, fontSize: 14, fontWeight: 700, textAlign: 'right' }}>{hex2(scanner.cur[c.addr] ?? -1)}</span>
              </div>
            ))}
          </>
        )}
      </div>
      <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
        <Button hotkey="B" onClick={back} disabled={!w.canBack}>Back</Button>
        <div style={{ flexGrow: 1 }} />
        <Button onClick={retry} disabled={!w.canRetry || !scanner.available()}>Not it, try again</Button>
        <Button kind="primary" hotkey="N" onClick={next} disabled={primaryOff} testId="wizard-primary" style={{ minWidth: 150, justifyContent: 'center' }}>{w.primary}</Button>
      </div>
    </Card>
  );
}
