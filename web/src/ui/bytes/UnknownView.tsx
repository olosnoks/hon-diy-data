/** Unknown bytes: what the analysis says about every byte and bit the map
 *  does not decode with confidence, with the owner's notes and confidence. */

import { useMemo, useState } from 'react';
import { Button, Card, mono, Note, Select, TextArea, TextInput, useTheme } from './controls';
import { hex2 } from './model/hex';
import type { MemoryScanner } from './model/scanner';
import { CONFIDENCES, editEntry, mergedEntries, type Confidence, type NotesStore, type UnknownEntry } from './model/unknown';
import { useScannerRevision } from './ScannerView';

export function UnknownView(props: { store: NotesStore; scanner: MemoryScanner; onPick: (addr: number) => void;
  onChange?: (entries: UnknownEntry[]) => void }) {
  const t = useTheme();
  const [rev, setRev] = useState(0);
  const entries = useMemo(() => mergedEntries(props.store), [props.store, rev]);
  const [filter, setFilter] = useState('');
  const [conf, setConf] = useState<'all' | Confidence>('all');
  const [sel, setSel] = useState<string | null>(null);
  useScannerRevision(props.scanner);
  const shown = entries.filter((e) => (conf === 'all' || e.confidence === conf)
    && (!filter || `${e.key} ${e.name} ${e.what} ${e.notes}`.toLowerCase().includes(filter.toLowerCase())));
  const edit = (key: string, change: Partial<Pick<UnknownEntry, 'notes' | 'confidence' | 'name'>>) => {
    editEntry(props.store, key, change);
    setRev((r) => r + 1);
    props.onChange?.(mergedEntries(props.store));
  };
  const current = entries.find((e) => e.key === sel) ?? null;
  const confColor = (c: Confidence) => (c === 'high' ? t.ok : c === 'medium' ? t.warn : c === 'low' ? t.unverified : t.comment);
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 380px', gap: 8, height: '100%', minHeight: 0 }}>
      <Card title={`Unknown bytes · ${shown.length} of ${entries.length}`} right={
        <div style={{ display: 'flex', gap: 8 }}>
          <TextInput aria-label="filter" value={filter} onChange={setFilter} placeholder="filter" style={{ width: 160 }} />
          <Select aria-label="confidence filter" value={conf} onChange={setConf}
            options={[['all', 'all'], ...CONFIDENCES.map((c) => [c, c] as const)]} />
        </div>
      }>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ ...mono, textAlign: 'left', fontSize: 12 }}>
              <th style={{ padding: 4 }}>ADDR</th><th>NAME</th><th>WHAT IT IS</th><th>NOW</th><th>CONFIDENCE</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((e) => {
              const on = e.key === sel;
              const v = props.scanner.cur[e.addr]!;
              const now = v < 0 ? '--' : e.bit === null ? hex2(v) : String((v >> e.bit) & 1);
              return (
                <tr key={e.key} onClick={() => { setSel(e.key); props.onPick(e.addr); }} aria-selected={on}
                  style={{ background: on ? t.sunk : 'transparent', color: t.fg, cursor: 'pointer', borderBottom: `2px solid ${t.sunk}` }}>
                  <td style={{ ...mono, fontWeight: 700, padding: '6px 4px', whiteSpace: 'nowrap', boxShadow: on ? `inset 5px 0 0 ${t.accent}` : 'none', paddingLeft: 10 }}>{e.bit === null ? e.key : `${e.key.split('.')[0]} b${e.bit}`}</td>
                  <td style={{ ...mono, fontWeight: 700 }}>{e.name}</td>
                  <td>{e.what}{e.notes ? <em> · {e.notes}</em> : null}</td>
                  <td style={{ ...mono, fontWeight: 700 }}>{now}</td>
                  <td><span style={{ ...mono, fontWeight: 700, color: confColor(e.confidence) }}>{e.confidence}</span></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>
      <Card title={current ? `${current.key} · ${current.name}` : 'Notes'}>
        {current ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <Note style={{ color: t.fg }}>{current.what}</Note>
            <Note><strong>Evidence:</strong> {current.evidence} <em>({current.source})</em></Note>
            <label style={{ fontWeight: 700 }}>Name
              <TextInput aria-label="name" value={current.name} onChange={(v) => edit(current.key, { name: v })} monospace style={{ width: '100%' }} />
            </label>
            <label style={{ fontWeight: 700 }}>Confidence{' '}
              <Select aria-label="confidence" value={current.confidence} onChange={(v) => edit(current.key, { confidence: v })}
                options={CONFIDENCES.map((c) => [c, c] as const)} />
            </label>
            <label style={{ fontWeight: 700 }}>Your notes
              <TextArea aria-label="notes" value={current.notes} onChange={(v) => edit(current.key, { notes: v })} rows={6} />
            </label>
            <div><Button onClick={() => props.onPick(current.addr)}>Show in the scanner</Button></div>
          </div>
        ) : <Note>Pick a byte. Seeded from the 2026-11-26 analysis (README and drive-bytes); your notes and confidence are kept on this device.</Note>}
      </Card>
    </div>
  );
}
