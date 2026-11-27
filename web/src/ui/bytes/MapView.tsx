/** Address map: the TOML text and a form over it, validation, verified /
 *  suspect marks with evidence, version bump, a diff against the saved map
 *  and the version hash the CYD checks. */

import { useMemo, useState } from 'react';
import { Button, Card, downloadFile, mono, Note, Select, StatusMark, TextArea, TextInput, useTheme } from './controls';
import { hex2 } from './model/hex';
import {
  bumpVersion, changeText, diffMaps, markChannel, parseMapText, setChannel, validateMap, type MapChannel, type ParsedMap, type Status,
} from './model/mapdoc';

export function tryParse(text: string): { map: ParsedMap | null; error: string } {
  try {
    return { map: parseMapText(text), error: '' };
  } catch (e) {
    return { map: null, error: (e as Error).message };
  }
}

function ChannelForm(props: { ch: MapChannel; onApply: (changes: Record<string, unknown>) => void; onMark: (s: Status, note: string) => void }) {
  const { ch } = props;
  const t = useTheme();
  const [addr, setAddr] = useState(`0x${hex2(ch.addr)}`);
  const [length, setLength] = useState(String(ch.length));
  const [endian, setEndian] = useState(ch.endian);
  const [formula, setFormula] = useState(ch.formula);
  const [units, setUnits] = useState(ch.units);
  const [label, setLabel] = useState(ch.label);
  const [notes, setNotes] = useState(ch.notes);
  const [note, setNote] = useState('');
  const [err, setErr] = useState('');
  const apply = () => {
    const a = Number(addr.trim().toLowerCase().startsWith('0x') ? parseInt(addr.trim().slice(2), 16) : Number(addr));
    const n = Number(length);
    if (!Number.isInteger(a) || a < 0 || a > 255) { setErr('addr must be 0x00-0xFF'); return; }
    if (!Number.isInteger(n) || n < 1 || n > 4) { setErr('length must be 1-4'); return; }
    setErr('');
    const changes: Record<string, unknown> = {};
    if (a !== ch.addr) changes.addr = a;
    if (n !== ch.length) changes.length = n;
    if (endian !== ch.endian) changes.endian = endian;
    if (formula !== ch.formula) changes.formula = formula;
    if (units !== ch.units) changes.units = units;
    if (label !== ch.label) changes.label = label;
    if (notes !== ch.notes) changes.notes = notes || null;
    // a new address or formula proves nothing yet
    if ('addr' in changes || 'length' in changes || 'endian' in changes || 'formula' in changes) changes.status = 'unverified';
    props.onApply(changes);
  };
  const row = { display: 'grid', gridTemplateColumns: '90px 1fr', gap: 8, alignItems: 'center', marginBottom: 6 } as const;
  return (
    <div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
        <strong style={{ ...mono, fontSize: 16 }}>{ch.name}</strong><StatusMark status={ch.status} />
      </div>
      <label style={row}>addr<TextInput aria-label="addr" value={addr} onChange={setAddr} monospace /></label>
      <label style={row}>length<TextInput aria-label="length" value={length} onChange={setLength} monospace /></label>
      <label style={row}>endian<Select aria-label="endian" value={endian} onChange={setEndian} options={[['big', 'big'], ['little', 'little']]} /></label>
      <label style={row}>formula<TextInput aria-label="formula" value={formula} onChange={setFormula} monospace /></label>
      <label style={row}>units<TextInput aria-label="units" value={units} onChange={setUnits} /></label>
      <label style={row}>label<TextInput aria-label="label" value={label} onChange={setLabel} /></label>
      <label style={row}>notes<TextInput aria-label="notes" value={notes} onChange={setNotes} /></label>
      {err && <Note style={{ color: t.alarm }}>{err}</Note>}
      <Button onClick={apply}>Apply to the text</Button>
      <Note>A new address, length, byte order or formula resets the channel to unverified.</Note>
      {ch.evidence && (
        <Note><strong>Evidence:</strong> {ch.evidence.method} {ch.evidence.result} {ch.evidence.date} · {ch.evidence.summary}</Note>
      )}
      <div style={{ marginTop: 8 }}>
        <TextInput aria-label="why" value={note} onChange={setNote} placeholder="why (goes into the evidence)" style={{ width: '100%', marginBottom: 6 }} />
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <Button onClick={() => props.onMark('verified', note)}>Mark verified</Button>
          <Button onClick={() => props.onMark('suspect', note)}>Mark suspect</Button>
          <Button onClick={() => props.onMark('unverified', note)}>Mark unverified</Button>
        </div>
      </div>
    </div>
  );
}

export function MapView(props: { savedText: string; text: string; setText: (t: string) => void; saveMap: ((t: string) => Promise<void>) | null;
  onSaved?: (t: string) => void }) {
  const t = useTheme();
  const { text, setText } = props;
  const parsed = useMemo(() => tryParse(text), [text]);
  const saved = useMemo(() => tryParse(props.savedText), [props.savedText]);
  const problems = useMemo(() => validateMap(text), [text]);
  const [sel, setSel] = useState<string | null>(null);
  const [msg, setMsg] = useState('');
  const [mode, setMode] = useState<'form' | 'text'>('form');
  const map = parsed.map;
  const ch = map?.channels.find((c) => c.name === sel) ?? null;
  const diff = map && saved.map ? diffMaps(saved.map, map) : null;
  const edit = (fn: (t: string) => string) => {
    try { setText(fn(text)); setMsg(''); } catch (e) { setMsg((e as Error).message); }
  };
  const save = async () => {
    if (!map) { setMsg(`not saved: ${parsed.error}`); return; }
    if (props.saveMap) {
      try { await props.saveMap(text); props.onSaved?.(text); setMsg(`saved: ${map.version} #${map.hash}`); } catch (e) { setMsg(`not saved: ${(e as Error).message}`); }
    } else {
      downloadFile('addressmap.toml', text, 'text/plain');
      props.onSaved?.(text);
      setMsg('downloaded addressmap.toml (no map store in this build)');
    }
  };
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 8, height: '100%', minHeight: 0 }}>
      <Card title={map ? `${map.version} · #${map.hash}` : 'Address map (does not load)'} right={
        <div style={{ display: 'flex', gap: 6 }}>
          <Button active={mode === 'form'} onClick={() => setMode('form')}>Channels</Button>
          <Button active={mode === 'text'} onClick={() => setMode('text')}>TOML</Button>
        </div>
      }>
        {mode === 'text' ? (
          <TextArea aria-label="address map TOML" value={text} onChange={setText} rows={30} style={{ height: '100%', minHeight: 420 }} />
        ) : map ? (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead><tr style={{ ...mono, fontSize: 12, textAlign: 'left' }}><th>NAME</th><th>ADDR</th><th>LEN</th><th>FORMULA</th><th>UNITS</th><th>STATUS</th></tr></thead>
            <tbody>
              {map.channels.map((c) => {
                const on = c.name === sel;
                return (
                  <tr key={c.name} onClick={() => setSel(c.name)} aria-selected={on}
                    style={{ cursor: 'pointer', background: on ? t.sunk : 'transparent', borderBottom: `1px solid ${t.line}`, outline: on ? `2px solid ${t.fg}` : 'none' }}>
                    <td style={{ ...mono, fontWeight: 700, padding: '5px 4px' }}>{c.name}</td>
                    <td style={mono}>0x{hex2(c.addr)}</td><td style={mono}>{c.length}</td>
                    <td style={{ ...mono, fontSize: 12 }}>{c.formula}</td><td>{c.units}</td><td><StatusMark status={c.status} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : <Note style={{ color: t.alarm }}>{parsed.error}</Note>}
      </Card>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minHeight: 0 }}>
        <Card title="Edit" style={{ flex: '1 1 auto' }}>
          {ch ? (
            <ChannelForm key={`${ch.name}:${text.length}`} ch={ch}
              onApply={(changes) => edit((tx) => (Object.keys(changes).length ? setChannel(tx, ch.name, changes) : tx))}
              onMark={(s, note) => edit((tx) => markChannel(tx, ch.name, s, {
                date: new Date().toISOString().slice(0, 19), method: 'manual', result: s === 'verified' ? 'pass' : s === 'suspect' ? 'fail' : '',
                summary: note, map_version: map!.version, map_hash: map!.hash,
              }))} />
          ) : <Note>Pick a channel, or edit the TOML directly. Marks keep the file's comments; the hash only moves when decoding changes.</Note>}
        </Card>
        <Card title="Check, version and save" style={{ flex: '0 0 auto', maxHeight: 300 }}>
          <div style={{ ...mono, fontSize: 13, marginBottom: 6 }} data-testid="map-validation">
            {problems.length === 0 ? 'OK  the map loads' : problems.map((p, i) => <div key={i} style={{ color: p.startsWith('note:') ? t.fg2 : t.alarm }}>{p.startsWith('note:') ? '' : 'NO  '}{p}</div>)}
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <Button onClick={() => edit(bumpVersion)}>Bump version</Button>
            <Button onClick={() => setText(props.savedText)} disabled={text === props.savedText}>Revert</Button>
            <Button kind="primary" onClick={() => void save()} disabled={!map || text === props.savedText}>Save map</Button>
          </div>
          {msg && <Note>{msg}</Note>}
          {diff && diff.changes.length > 0 && (
            <div style={{ marginTop: 6 }}>
              <strong>Changes against the saved map{diff.decodeChanged ? ` (decoding changed: #${saved.map!.hash} → #${map!.hash})` : ' (decoding unchanged)'}:</strong>
              <ul style={{ ...mono, fontSize: 12, margin: '4px 0', paddingLeft: 18 }}>
                {diff.changes.slice(0, 40).map((c, i) => <li key={i}>{changeText(c)}</li>)}
              </ul>
              {diff.decodeChanged && map!.version === saved.map!.version && <Note style={{ color: t.warn }}>Decoding changed but the version did not: bump it, the version goes into every log.</Note>}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
