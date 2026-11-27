/** Rank addresses: the correlation finder on the sweep history. Rank against
 *  a channel of the map, a shape you make by hand, or a typed value; give the
 *  best address to a channel through the map editor (rawtools.py RankCard,
 *  backends.py PollerMapper.rank / assign). */

import { useState } from 'react';
import { Button, Card, mono, Note, Select, TextInput, useTheme } from './controls';
import { candLabel, findValue, matchFormula, rankAgainstChannel, rankShape, type Candidate } from './model/correlate';
import { hex2 } from './model/hex';
import { assignAddress, type ParsedMap } from './model/mapdoc';
import type { MemoryScanner } from './model/scanner';
import { mappedAt, useScannerRevision } from './ScannerView';

export const MIN_SWEEPS = 5;

export interface RankRow {
  candidate: Candidate; label: string; score: number; r: number; formula: string; lo: number; hi: number; mapped: string; note: string;
}

export interface RankRef { key: string; label: string; kind: 'channel' | 'shape' | 'value'; verified: boolean }

export function rankReferences(map: ParsedMap | null): RankRef[] {
  const chans = (map?.channels ?? []).filter((c) => c.kind === 'value')
    .map((c) => ({ key: c.name, label: c.label, kind: 'channel' as const, verified: c.status === 'verified' }))
    .sort((a, b) => Number(b.verified) - Number(a.verified));
  return [...chans, { key: 'shape:rise', label: 'Rises steadily', kind: 'shape', verified: false },
    { key: 'shape:fall', label: 'Falls steadily', kind: 'shape', verified: false },
    { key: 'value', label: 'A value you type (tach, meter)', kind: 'value', verified: false }];
}

/** The finder on the scanner's sweeps. Returns rows, or a reason when empty. */
export function rankNow(scanner: MemoryScanner, map: ParsedMap | null, ref: string, top = 8,
  typed?: { target: number; formula: string }): { rows: RankRow[]; why: string } {
  const rec = scanner.recording();
  if (rec.length < (ref === 'value' ? 1 : MIN_SWEEPS)) {
    return { rows: [], why: `${rec.length} sweeps so far: sweep for a few seconds while the signal changes, then rank` };
  }
  const span = (c: Candidate) => {
    const addrs = Array.from({ length: c.width }, (_, k) => Math.min(255, c.addr + k));
    return [Math.min(...addrs.map((a) => scanner.minv[a]!)), Math.max(...addrs.map((a) => scanner.maxv[a]!))] as const;
  };
  try {
    if (ref === 'value') {
      if (!typed || !Number.isFinite(typed.target)) return { rows: [], why: 'type the value the gauge shows' };
      const hits = findValue(rec, typed.target, typed.formula || 'x', 0, 0.05, [1, 2], top);
      const rows = hits.map((h) => {
        const [lo, hi] = span(h.candidate);
        return { candidate: h.candidate, label: candLabel(h.candidate), score: Math.max(0, 1 - h.error / Math.max(1e-9, Math.abs(typed.target) * 0.05)),
          r: 1, formula: typed.formula, lo, hi, mapped: mappedAt(map, h.candidate.addr)?.name ?? '',
          note: `reads ${Number(h.decoded.toPrecision(4))}, moves ${Number(h.spread.toPrecision(3))}` };
      });
      return { rows, why: rows.length ? '' : 'nothing reads that value with this formula' };
    }
    const shape = ref.startsWith('shape:');
    if (!shape && !map) return { rows: [], why: 'the address map does not load' };
    const t = rec.t;
    const matches = shape ? rankShape(rec, ref.split(':')[1] as 'rise' | 'fall', t[0]!, t[t.length - 1]!, { top })
      : rankAgainstChannel(rec, map!, ref, { top });
    const rows = matches.map((m) => {
      const [lo, hi] = span(m.candidate);
      return { candidate: m.candidate, label: candLabel(m.candidate), score: m.score, r: shape ? m.rho : m.r, formula: shape ? '' : matchFormula(m),
        lo, hi, mapped: mappedAt(map, m.candidate.addr)?.name ?? '', note: m.notes.join('; ') };
    });
    return { rows, why: rows.length ? '' : 'nothing moved with it in these sweeps' };
  } catch (e) {
    return { rows: [], why: `!! ${(e as Error).message}` };
  }
}

export function RankView(props: { scanner: MemoryScanner; map: ParsedMap | null; mapText: string; setMapText: (t: string) => void;
  onPick: (addr: number) => void; confirm?: (text: string) => boolean }) {
  const t = useTheme();
  const { scanner, map } = props;
  useScannerRevision(scanner);
  const refs = rankReferences(map);
  const [ref, setRef] = useState(refs[0]?.key ?? 'shape:rise');
  const [rows, setRows] = useState<RankRow[]>([]);
  const [why, setWhy] = useState('');
  const [ranked, setRanked] = useState(false);
  const [sel, setSel] = useState(0);
  const [target, setTarget] = useState('');
  const [formula, setFormula] = useState('x');
  const [assignTo, setAssignTo] = useState(map?.channels[0]?.name ?? '');
  const [msg, setMsg] = useState('');
  const confirm = props.confirm ?? ((text: string) => window.confirm(text));
  const cur = refs.find((r) => r.key === ref) ?? refs[0]!;

  const instruction = cur.kind === 'shape'
    ? `Clear changes (C), then move one thing steadily ${ref.endsWith('rise') ? 'up' : 'down'} while it sweeps (a slow pedal press, the engine warming). Then Rank now.`
    : cur.kind === 'value'
      ? 'Hold the condition steady (tach at 3000, meter reading), type the value and the formula to decode with, then Rank now.'
      : `Sweep while ${cur.label} changes. The addresses that moved with it are listed; its own bytes are left out.${cur.verified ? '' : ' It is unverified itself: a weak reference.'}`;

  const doRank = () => {
    const out = rankNow(scanner, map, ref, 8, { target: Number(target), formula });
    setRows(out.rows); setWhy(out.why); setRanked(true); setSel(0);
    if (out.rows[0]) props.onPick(out.rows[0].candidate.addr);
  };
  const row = rows[sel];
  const assign = () => {
    if (!row || !assignTo) return;
    const ch = map?.channels.find((c) => c.name === assignTo);
    const was = ch ? `${ch.label} (${ch.name}) now 0x${hex2(ch.addr)}` : assignTo;
    if (!confirm(`${was}\n\nwill read ${row.label} from now on. It is marked unverified and the map's version moves on. The formula is not changed.`)) return;
    try {
      props.setMapText(assignAddress(props.mapText, assignTo, row.candidate.addr, row.candidate.width, row.candidate.endian));
      setMsg(`${assignTo} now reads ${row.label} (unverified; save the map on the Address map tab)`);
    } catch (e) { setMsg(`!! ${(e as Error).message}`); }
  };

  return (
    <Card title="Rank addresses · what followed a signal">
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <Select aria-label="reference" value={ref} onChange={(v) => { setRef(v); setRows([]); setRanked(false); }} style={{ minWidth: 280 }}
          options={refs.map((r) => [r.key, r.kind === 'channel' ? `Follows: ${r.label}${r.verified ? '  (verified)' : '  (unverified)'}` : r.label] as const)} />
        {cur.kind === 'value' && <>
          <TextInput aria-label="typed value" value={target} onChange={setTarget} placeholder="3000" monospace style={{ width: 100 }} />
          <TextInput aria-label="decode formula" value={formula} onChange={setFormula} placeholder="x / 4" monospace style={{ width: 140 }} />
        </>}
        <Button hotkey="G" kind="primary" onClick={doRank} disabled={!scanner.available() && scanner.recording().length === 0}>Rank now</Button>
        <span style={{ ...mono, fontSize: 13 }}>{scanner.recording().length} sweeps in the history</span>
      </div>
      <Note style={{ color: t.fg }}>{instruction}</Note>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
        <thead><tr style={{ ...mono, fontSize: 12, textAlign: 'left' }}><th>ADDRESS</th><th>FOLLOWS IT</th><th>RANGE</th><th>NOW</th><th>FIT / NOTE</th><th style={{ textAlign: 'right' }}>IN THE MAP AS</th></tr></thead>
        <tbody>
          {rows.map((r, i) => {
            const on = i === sel;
            const v = scanner.cur[r.candidate.addr]!;
            return (
              <tr key={r.label} onClick={() => { setSel(i); props.onPick(r.candidate.addr); }} aria-selected={on}
                style={{ background: on ? t.fg : 'transparent', color: on ? t.bg : t.fg, cursor: 'pointer', borderBottom: `2px solid ${t.sunk}` }}>
                <td style={{ ...mono, fontWeight: 700, padding: '6px 4px' }}>{r.label}</td>
                <td>
                  <span style={{ display: 'inline-block', width: 110, height: 12, border: `2px solid ${on ? t.bg : t.fg}`, verticalAlign: 'middle', position: 'relative' }}>
                    <span style={{ position: 'absolute', inset: 0, width: `${Math.min(1, r.score) * 100}%`, background: on ? t.bg : t.fg }} />
                  </span>
                  <span style={{ ...mono, fontWeight: 700, marginLeft: 6 }}>{r.score.toFixed(2)}{r.r >= 0 ? '↑' : '↓'}</span>
                </td>
                <td style={mono}>{r.lo}–{r.hi}</td>
                <td style={mono}>{v >= 0 ? hex2(v) : '--'}</td>
                <td style={{ ...mono, fontSize: 12 }}>{[r.formula ? `fits ${r.formula}` : '', r.note].filter(Boolean).join('; ')}</td>
                <td style={{ textAlign: 'right' }}>{r.mapped || '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {!rows.length && <Note>{ranked ? why || 'Nothing moved with it in these sweeps.' : 'Not ranked yet.'}</Note>}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8 }}>
        <span>Give {row ? <strong style={mono}>{row.label}</strong> : 'the selected address'} to</span>
        <Select aria-label="assign to channel" value={assignTo} onChange={setAssignTo}
          options={(map?.channels ?? []).map((c) => [c.name, `${c.label} (${c.name}) now 0x${hex2(c.addr)}${c.status === 'verified' ? '  verified' : ''}`] as const)} />
        <Button kind="primary" onClick={assign} disabled={!row || !map}>Assign to channel…</Button>
      </div>
      {msg && <Note style={{ color: t.fg }}>{msg}</Note>}
    </Card>
  );
}
