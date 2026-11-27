/** Scanner: the live hex grid of every read address with change heat, the
 *  byte inspector with its per-bit view and trace, and the hex console
 *  (rawtools.py HexGrid, ByteInspector, ConsoleCard). */

import { useState, useSyncExternalStore, type ReactElement } from 'react';
import { Button, Card, Check, downloadFile, mono, Note, TextInput, useTheme } from './controls';
import { fmtHex, hex2, parseHex } from './model/hex';
import type { ParsedMap } from './model/mapdoc';
import type { MemoryScanner } from './model/scanner';
import type { UnknownEntry } from './model/unknown';

export function useScannerRevision(scanner: MemoryScanner): number {
  return useSyncExternalStore((fn) => scanner.subscribe(fn), () => scanner.revision);
}

/** (channel, verified) of the map channel that reads `addr`. */
export function mappedAt(map: ParsedMap | null, addr: number): { name: string; status: string } | null {
  const ch = map?.channels.find((c) => c.addr <= addr && addr < c.addr + c.length);
  return ch ? { name: ch.name, status: ch.status } : null;
}

export function HexGrid(props: { scanner: MemoryScanner; map: ParsedMap | null; sel: number; onSelect: (a: number) => void }) {
  const t = useTheme();
  const { scanner: sc, map, sel } = props;
  useScannerRevision(sc);
  const cells: ReactElement[] = [];
  for (let a = 0; a < 256; a++) {
    const v = sc.cur[a]!;
    const fresh = sc.fresh[a]! > 0;
    const changed = sc.changes[a]! > 0;
    const m = mappedAt(map, a);
    const guess = m !== null && m.status !== 'verified';
    cells.push(
      <button
        key={a}
        data-addr={a}
        aria-label={`0x${hex2(a)} = ${v >= 0 ? hex2(v) : 'not read'}${fresh ? ', changing now' : changed ? ', changed earlier' : ''}`}
        onClick={() => props.onSelect(a)}
        style={{
          ...mono, fontSize: 14, fontWeight: fresh ? 800 : changed ? 700 : 500, padding: 0, height: 26, borderRadius: 0, cursor: 'pointer',
          background: fresh ? t.warn : changed ? t.sunk : 'transparent', color: fresh ? t.onWarn : changed ? t.fg : t.fg2,
          border: `1px solid ${t.sunk}`, outline: a === sel ? `3px solid ${t.fg}` : guess ? `2px dashed ${fresh ? t.onWarn : t.unverified}` : 'none',
          outlineOffset: a === sel ? -3 : -4, boxShadow: changed && !fresh ? `inset 0 -4px 0 ${t.accent}` : 'none',
        }}
      >
        {v >= 0 ? hex2(v) : '--'}
      </button>,
    );
  }
  return (
    <div role="grid" aria-label="RAM 0x00-0xFF" style={{ display: 'grid', gridTemplateColumns: '52px repeat(16, minmax(26px, 1fr))', background: t.panel, border: `2px solid ${t.rule}`, padding: 2 }}>
      <span />
      {Array.from({ length: 16 }, (_, c) => <span key={c} style={{ ...mono, fontSize: 13, fontWeight: 700, textAlign: 'center' }}>x{c.toString(16).toUpperCase()}</span>)}
      {Array.from({ length: 16 }, (_, r) => [
        <span key={`h${r}`} style={{ ...mono, fontSize: 13, fontWeight: 700, textAlign: 'right', paddingRight: 6, lineHeight: '26px' }}>0x{r.toString(16).toUpperCase()}0</span>,
        ...cells.slice(r * 16, r * 16 + 16),
      ])}
    </div>
  );
}

export function Legend() {
  const t = useTheme();
  const box = { ...mono, display: 'inline-block', width: 34, height: 24, lineHeight: '24px', textAlign: 'center' as const, fontWeight: 700, marginRight: 6 };
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, fontSize: 13, fontWeight: 700, margin: '6px 0' }}>
      <span><span style={{ ...box, background: t.warn, color: t.onWarn }}>3F</span>changing now</span>
      <span><span style={{ ...box, background: t.sunk, boxShadow: `inset 0 -4px 0 ${t.accent}` }}>3F</span>changed earlier</span>
      <span><span style={{ ...box, outline: `2px dashed ${t.unverified}`, outlineOffset: -3 }}>3F</span>in address map, unverified</span>
      <span><span style={{ ...box, outline: `3px solid ${t.fg}`, outlineOffset: -3 }}>3F</span>selected</span>
    </div>
  );
}

function Spark(props: { values: number[] }) {
  const t = useTheme();
  const v = props.values.slice(-120);
  if (v.length < 2) return <div style={{ height: 40, border: `2px solid ${t.line}` }} />;
  const lo = Math.min(...v), hi = Math.max(Math.max(...v), lo + 1);
  const pts = v.map((x, i) => `${(i / 119) * 100 + (100 - ((v.length - 1) / 119) * 100)},${36 - ((x - lo) / (hi - lo)) * 32}`).join(' ');
  return (
    <svg viewBox="0 0 100 40" preserveAspectRatio="none" style={{ width: '100%', height: 40, border: `2px solid ${t.line}`, background: t.bg }} aria-label="trace of the last sweeps">
      <polyline points={pts} fill="none" stroke={t.fg} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** One row of 0/1 per sweep for each bit (the bit-field explorer's strips). */
function BitStrips(props: { values: number[] }) {
  const t = useTheme();
  const v = props.values.slice(-60);
  return (
    <div aria-label="bit history" style={{ display: 'grid', gap: 2 }}>
      {[7, 6, 5, 4, 3, 2, 1, 0].map((b) => (
        <div key={b} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ ...mono, fontSize: 12, width: 22 }}>b{b}</span>
          <div style={{ display: 'flex', flex: 1, height: 10, background: t.bg, border: `1px solid ${t.line}` }}>
            {v.map((x, i) => <span key={i} style={{ flex: 1, background: (x >> b) & 1 ? t.fg : 'transparent' }} />)}
          </div>
        </div>
      ))}
    </div>
  );
}

export function ByteInspector(props: { scanner: MemoryScanner; map: ParsedMap | null; addr: number; unknown?: UnknownEntry[] }) {
  const t = useTheme();
  const { scanner: sc, addr } = props;
  useScannerRevision(sc);
  const v = sc.cur[addr]!;
  const known = v >= 0;
  const hist = sc.history(addr);
  const m = mappedAt(props.map, addr);
  const notes = (props.unknown ?? []).filter((u) => u.addr === addr);
  return (
    <Card title="Byte">
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
        <span style={{ ...mono, fontSize: 32, fontWeight: 800 }}>0x{hex2(addr)}</span>
        <span style={{ flex: 1 }} />
        <span style={{ ...mono, fontSize: 32, fontWeight: 800 }} data-testid="inspector-hex">{known ? hex2(v) : '--'}</span>
        <span style={{ ...mono, fontSize: 18, fontWeight: 700 }}>{known ? v : ''}</span>
      </div>
      <div style={{ display: 'flex', gap: 4, alignItems: 'center', margin: '8px 0' }} aria-label="bits 7 to 0">
        {[7, 6, 5, 4, 3, 2, 1, 0].map((b) => {
          const on = known && (v >> b) & 1;
          return <span key={b} title={`bit ${b}`} style={{ ...mono, width: 30, height: 30, lineHeight: '26px', textAlign: 'center', fontWeight: 700,
            border: `2px solid ${t.rule}`, boxSizing: 'border-box', background: on ? t.fg : t.panel, color: on ? t.bg : t.fg }}>{on ? 1 : 0}</span>;
        })}
        <span style={{ ...mono, fontSize: 13, fontWeight: 700, marginLeft: 6 }}>bit 7 → 0</span>
      </div>
      <Spark values={hist} />
      <div style={{ ...mono, display: 'flex', justifyContent: 'space-between', fontSize: 13, fontWeight: 700, marginTop: 6 }}>
        <span>{known ? `min ${sc.minv[addr]}` : 'min --'}</span>
        <span>{known ? `max ${sc.maxv[addr]}` : 'max --'}</span>
        <span>{sc.changes[addr]} changes</span>
      </div>
      <div style={{ fontWeight: 700, margin: '6px 0' }}>
        {m ? `Address map: ${m.name} · ${m.status === 'verified' ? 'verified' : m.status === 'suspect' ? 'suspect' : 'unverified guess'}` : 'Not in the address map'}
      </div>
      {notes.map((n) => (
        <Note key={n.key}><strong>{n.key} {n.name}</strong> ({n.confidence}): {n.what}{n.notes ? ` · ${n.notes}` : ''}</Note>
      ))}
      <BitStrips values={hist} />
    </Card>
  );
}

export function HexConsole(props: { scanner: MemoryScanner }) {
  const t = useTheme();
  const sc = props.scanner;
  useScannerRevision(sc);
  const [tx, setTx] = useState('20 05 14 01');
  const [sum, setSum] = useState(true);
  const send = () => {
    let data: Uint8Array;
    try { data = parseHex(tx); } catch { sc.setConsole('!! not hex'); return; }
    void sc.send(data, sum);
  };
  return (
    <Card title="Hex console">
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <span style={{ ...mono, fontWeight: 700 }}>TX</span>
        <TextInput aria-label="frame to send" value={tx} onChange={setTx} onEnter={send} monospace style={{ flex: 1 }} />
        <Check checked={sum} onChange={setSum}>+ checksum</Check>
        <Button active={sc.expert} onClick={() => sc.setExpert(!sc.expert)} disabled={!sc.available()}
          title="Expert unlock: let the console send frames that are not reads. Every such frame is written to the log header.">Expert</Button>
        <Button testId="send" kind="primary" onClick={send} disabled={!sc.available()}>Send</Button>
      </div>
      <div style={{ ...mono, fontWeight: 700, marginTop: 8, color: t.fg, wordBreak: 'break-all' }} data-testid="console-rx">RX  {sc.console() || '—'}</div>
      {sc.expert && <Note>Expert is on: frames other than reads go out as typed ({sc.expertLog.length} sent).</Note>}
    </Card>
  );
}

export function ScannerToolbar(props: { scanner: MemoryScanner; demo: boolean }) {
  const sc = props.scanner;
  useScannerRevision(sc);
  const status = sc.available()
    ? `0x00–0xFF · sweep ${sc.sweeps} · ${sc.sweepS.toFixed(1)} s / sweep${props.demo ? ' · demo RAM' : ''}`
    : sc.obd2Only() ? 'OBD2 only · Honda reads are off' : 'not connected · click the ECU chip';
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
      <Button testId="sweep" hotkey="S" active={sc.sweeping} onClick={() => sc.setSweeping(!sc.sweeping)} disabled={!sc.available()}>
        {sc.sweeping ? 'Sweeping' : 'Sweep'}
      </Button>
      <Button hotkey="C" onClick={() => sc.clearChanges()}>Clear changes</Button>
      <span style={{ ...mono, fontSize: 13, fontWeight: 700, flex: 1 }} data-testid="scanner-status">{status}</span>
      <Button onClick={() => {
        const d = new Date();
        const p = (n: number) => String(n).padStart(2, '0');
        downloadFile(`scan_${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}.txt`, sc.exportText(d), 'text/plain');
      }}>Export</Button>
    </div>
  );
}

export { fmtHex };
