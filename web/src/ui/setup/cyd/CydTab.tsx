/**
 * Setup > CYD black box (hondalog/ui/screens/handheld.py): one Connect for
 * the whole tab (the port stays open while you move between the views):
 * Status, SD card (browse, download, upload, delete), Config (HANDHELD.CFG
 * as a form or as text), Map (push the address map; upload the offline road
 * map with resume), Console, Install firmware.
 */

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { BAUDS, checkImage, CHIP_NAME, DEFAULT_BAUD, eta, type FlashProgress } from '../../../core/cyd/flash';
import { Button, Card, Check, downloadFile, mono, Note, ProgressBlock, readFileAsBytes, Select, TextArea, TextInput, useTheme } from '../../bytes/controls';
import type { CydClient, CydPortChoice, CydState } from './client';
import {
  canRemove, cfgParse, cfgProblems, cfgSetValue, DEFAULT_CFG, fmtBytes, fmtDur, KNOWN_KEYS, progressText, splitPath, statusRows, uploadTarget,
} from './helpers';

type View = 'status' | 'files' | 'config' | 'map' | 'console' | 'install';
const VIEWS: [View, string][] = [['status', 'Status'], ['files', 'SD card'], ['config', 'Config'], ['map', 'Map'], ['console', 'Console'], ['install', 'Install firmware']];

interface Prog { stage: string; pct: number | null; detail: string; kind: '' | 'ok' | 'err' }

/** Confirmations go through this (a test replaces it). */
export type Confirm = (text: string) => boolean;

interface Ctx { client: CydClient; connected: boolean; info: Record<string, string>; say: (t: string) => void; confirm: Confirm; busy: boolean;
  run: <T>(fn: () => Promise<T>) => Promise<T> }

const now = () => performance.now() / 1000;

function Status({ ctx }: { ctx: Ctx }) {
  const t = useTheme();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [st, setSt] = useState<Record<string, any>>({});
  const [info, setInfo] = useState('Not connected.');
  const inflight = useRef(false);
  useEffect(() => {
    if (!ctx.connected) { setSt({}); setInfo('Not connected.'); return; }
    const poll = async () => {
      if (inflight.current || ctx.busy) return;
      inflight.current = true;
      try {
        setSt(await ctx.client.stat());
        const h = ctx.info;
        setInfo(`${h.id ?? '?'} · firmware ${h.fw ?? '?'} · ${h.variant ?? '?'} build · map #${h.map ?? '?'} · refreshed ${new Date().toLocaleTimeString()}`);
      } catch (e) { setInfo(`Status: ${(e as Error).message}`); } finally { inflight.current = false; }
    };
    void poll();
    const id = setInterval(() => void poll(), 1000);
    return () => clearInterval(id);
  }, [ctx.connected, ctx.client, ctx.info, ctx.busy]);
  const rows = statusRows(st);
  const sections = [...new Set(rows.map((r) => r[0]))];
  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 10 }}>
        {sections.map((sec) => (
          <div key={sec} style={{ border: `2px solid ${t.rule}`, background: t.panel, padding: '8px 12px' }}>
            <div style={{ color: t.fg2, fontWeight: 600, marginBottom: 4 }}>{sec.toUpperCase()}</div>
            {rows.filter((r) => r[0] === sec).map(([, label, value, good]) => (
              <div key={label} style={{ display: 'grid', gridTemplateColumns: '150px 1fr', gap: 8 }}>
                <span style={{ color: t.fg2 }}>{label}</span>
                <span data-testid={`stat-${label}`} style={{ ...mono, fontWeight: 700, color: good === true ? t.ok : good === false ? t.alarm : value.startsWith('--') ? t.fg2 : t.fg }}>{value}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
      <Note>{info}</Note>
      <div style={{ display: 'flex', gap: 8 }}>
        <Button disabled={!ctx.connected} onClick={() => void ctx.run(() => ctx.client.mark()).then(() => ctx.say('Marker set on the handheld'), (e) => ctx.say(String(e.message)))}>Drop a marker</Button>
        <Button disabled={!ctx.connected} onClick={() => void ctx.run(() => ctx.client.setTime()).then((r) => ctx.say(r === 'ok' ? 'Clock set from this PC' : 'The handheld keeps GPS time: clock not changed'), (e) => ctx.say(String(e.message)))}>Set clock from this PC</Button>
        <Button disabled={!ctx.connected} onClick={() => { if (ctx.confirm('Restart the handheld now?')) void ctx.run(() => ctx.client.reboot()).then(() => ctx.say('The handheld is restarting'), (e) => ctx.say(String(e.message))); }}>Restart handheld</Button>
      </div>
    </div>
  );
}

interface Node { path: string; name: string; dir: boolean; size: number; mtime: number; open?: boolean; loaded?: boolean; children?: Node[] }

function Files({ ctx }: { ctx: Ctx }) {
  const t = useTheme();
  const [root, setRoot] = useState<Node | null>(null);
  const [sel, setSel] = useState<Node | null>(null);
  const [prog, setProg] = useState<Prog | null>(null);
  const [, force] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const expand = async (n: Node) => {
    n.open = !n.open;
    if (n.open && !n.loaded) {
      try {
        const [files, dirs] = await ctx.run(() => ctx.client.ls(n.path));
        n.children = [
          ...dirs.map((d) => ({ path: d, name: (d.split('/').filter(Boolean).pop() ?? d) + '/', dir: true, size: 0, mtime: 0 })),
          ...files.map(([p, size, mtime]) => ({ path: p, name: p.split('/').pop() ?? p, dir: false, size, mtime })),
        ];
        n.loaded = true;
      } catch (e) { n.open = false; ctx.say(`List failed: ${(e as Error).message}`); }
    }
    force((x) => x + 1);
  };
  const reload = () => { const r: Node = { path: '/', name: '/', dir: true, size: 0, mtime: 0 }; setRoot(r); setSel(null); void expand(r); };
  useEffect(() => { if (ctx.connected) reload(); else { setRoot(null); setSel(null); setProg(null); } }, [ctx.connected]); // eslint-disable-line react-hooks/exhaustive-deps

  const download = async () => {
    if (!sel || sel.dir) return;
    const t0 = now();
    try {
      const data = await ctx.run(() => ctx.client.get(sel.path, (d, tot) => { const [p, txt] = progressText(d, tot || sel.size, now() - t0); setProg({ stage: `Downloading ${sel.name}`, pct: p, detail: txt, kind: '' }); }));
      downloadFile(sel.name, data);
      setProg({ stage: `Done: ${sel.name}`, pct: 100, detail: `${fmtBytes(data.length)} saved`, kind: 'ok' });
    } catch (e) { setProg({ stage: `Failed: ${sel.name}`, pct: null, detail: (e as Error).message, kind: 'err' }); }
  };
  const upload = async (file: File) => {
    const [target, why] = uploadTarget(file.name);
    if (!target) { ctx.say(why); return; }
    const data = await readFileAsBytes(file);
    const t0 = now();
    try {
      const r = await ctx.run(() => ctx.client.put(target, data, (d, tot) => { const [p, txt] = progressText(d, tot, now() - t0); setProg({ stage: `Uploading ${file.name} to ${target}`, pct: p, detail: txt, kind: '' }); }));
      setProg({ stage: `Done: ${target}`, pct: 100, detail: fmtBytes(data.length) + (r ? ` · ${r}` : ''), kind: 'ok' });
      reload();
    } catch (e) { setProg({ stage: `Failed: ${target}`, pct: null, detail: (e as Error).message, kind: 'err' }); }
  };
  const del = async () => {
    if (!sel || sel.dir) return;
    const parts = splitPath(sel.path);
    if (!parts || !canRemove(parts)) { ctx.say('The handheld lets MAP/, DRIVES/ and the settings files be deleted, not this one.'); return; }
    if (!ctx.confirm(`Delete ${sel.path} from the handheld's SD card?`)) return;
    try { await ctx.run(() => ctx.client.rm(sel.path)); ctx.say(`${sel.path} deleted`); reload(); } catch (e) { ctx.say((e as Error).message); }
  };
  const rows: ReactNode[] = [];
  const walk = (n: Node, depth: number) => {
    const on = sel === n;
    rows.push(
      <tr key={n.path + depth} onClick={() => setSel(n)} onDoubleClick={() => n.dir && void expand(n)} aria-selected={on}
        style={{ background: on ? t.accent : depth % 2 ? t.sunk : 'transparent', color: on ? t.onAccent : t.fg, cursor: 'pointer' }}>
        <td style={{ ...mono, paddingLeft: 6 + depth * 18 }}>
          {n.dir && <button aria-label={`${n.open ? 'close' : 'open'} ${n.name}`} onClick={(e) => { e.stopPropagation(); void expand(n); }}
            style={{ ...mono, background: 'transparent', color: 'inherit', border: 'none', cursor: 'pointer', width: 18 }}>{n.open ? '▾' : '▸'}</button>}
          {n.name}
        </td>
        <td style={{ ...mono, textAlign: 'right' }}>{n.dir ? '' : fmtBytes(n.size)}</td>
        <td style={mono}>{n.mtime ? new Date(n.mtime * 1000).toISOString().slice(0, 16).replace('T', ' ') : ''}</td>
      </tr>,
    );
    if (n.open) (n.children ?? []).forEach((c) => walk(c, depth + 1));
  };
  if (root) walk(root, 0);
  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
        <Button disabled={!ctx.connected} onClick={reload}>Refresh</Button>
        <Button kind="primary" disabled={!ctx.connected || !sel || sel.dir} onClick={() => void download()}>Download</Button>
        <Button disabled={!ctx.connected} onClick={() => fileInput.current?.click()}>Upload</Button>
        <Button disabled={!ctx.connected || !sel || sel.dir} onClick={() => void del()}>Delete</Button>
        <input ref={fileInput} type="file" hidden aria-label="file to upload" onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ''; }} />
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14, border: `2px solid ${t.rule}` }}>
        <thead><tr style={{ background: t.sunk, textAlign: 'left' }}><th style={{ padding: 6 }}>Name</th><th style={{ textAlign: 'right' }}>Size</th><th>Modified</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
      {prog && <ProgressBlock {...prog} />}
      <Note>Upload writes to MAP/ and the two settings files (HANDHELD.CFG, HANDHELD.MAP); delete works in MAP/ and DRIVES/. The drive being recorded cannot be read or deleted: end it first.</Note>
    </div>
  );
}

function Config({ ctx }: { ctx: Ctx }) {
  const [text, setText] = useState(DEFAULT_CFG);
  const [mode, setMode] = useState<'form' | 'raw'>('form');
  const [prog, setProg] = useState<Prog | null>(null);
  const entries = cfgParse(text);
  const read = async () => {
    const t0 = now();
    try {
      const r = await ctx.run(() => ctx.client.cfgGet((d, tot) => { const [p, txt] = progressText(d, tot, now() - t0); setProg({ stage: 'Reading HANDHELD.CFG', pct: p, detail: txt, kind: '' }); }));
      setText(new TextDecoder().decode(r));
      setProg({ stage: 'Done: HANDHELD.CFG read', pct: 100, detail: `${r.length} bytes`, kind: 'ok' });
    } catch (e) {
      const m = (e as Error).message;
      setProg({ stage: 'Failed: could not read HANDHELD.CFG', pct: null, detail: m + (/no such file/.test(m) ? '. The card has none yet: the defaults are shown, write them to create it.' : ''), kind: 'err' });
    }
  };
  const write = async () => {
    const bad = cfgProblems(text, KNOWN_KEYS);
    if (bad.length && !ctx.confirm(`The handheld will skip these lines:\n\n${bad.slice(0, 8).join('\n')}\n\nWrite the file anyway?`)) return;
    const data = new TextEncoder().encode(text);
    const t0 = now();
    try {
      const r = await ctx.run(() => ctx.client.cfgPut(data, (d, tot) => { const [p, txt] = progressText(d, tot, now() - t0); setProg({ stage: 'Writing HANDHELD.CFG', pct: p, detail: txt, kind: '' }); }));
      setProg({ stage: 'Done: HANDHELD.CFG written', pct: 100, detail: `${r || 'accepted'}. Restart the handheld to use it.`, kind: 'ok' });
    } catch (e) { setProg({ stage: 'Failed: HANDHELD.CFG not changed', pct: null, detail: (e as Error).message, kind: 'err' }); }
  };
  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
        <Button active={mode === 'form'} onClick={() => setMode('form')}>Form</Button>
        <Button active={mode === 'raw'} onClick={() => setMode('raw')}>Raw text</Button>
        <Button disabled={!ctx.connected} onClick={() => void read()}>Read from handheld</Button>
        <Button kind="primary" disabled={!ctx.connected} onClick={() => void write()}>Write to handheld</Button>
        <Button onClick={() => setText(DEFAULT_CFG)}>Defaults</Button>
        <Button disabled={!ctx.connected} onClick={() => { if (ctx.confirm('Restart the handheld now?')) void ctx.run(() => ctx.client.reboot()).then(() => ctx.say('The handheld is restarting')); }}>Restart handheld</Button>
      </div>
      {mode === 'raw' ? <TextArea aria-label="HANDHELD.CFG" value={text} onChange={setText} rows={24} /> : (
        <div style={{ display: 'grid', gridTemplateColumns: '160px 260px 1fr', gap: '6px 10px', alignItems: 'center' }}>
          {entries.map((e) => [
            <span key={`${e.key}k`} style={{ ...mono, fontWeight: 700 }}>{e.key}</span>,
            <TextInput key={`${e.key}v`} aria-label={e.key} monospace value={e.enabled ? e.value : ''} placeholder={e.enabled ? '' : "(not set: the handheld's own default)"}
              onChange={(v) => setText(cfgSetValue(text, e.key, v.trim(), !!v.trim()))} />,
            <Note key={`${e.key}c`} style={{ margin: 0 }}>{e.comment}</Note>,
          ])}
        </div>
      )}
      {prog && <ProgressBlock {...prog} />}
      <Note>Read the file from the handheld, change it, write it back. The handheld checks the file (length and checksum) before it replaces the old one, which it keeps as HANDHELD.CBK. Settings are used from the next power-up.</Note>
    </div>
  );
}

function MapPush({ ctx, mapText, mapLabel }: { ctx: Ctx; mapText: string; mapLabel: string }) {
  const [prog, setProg] = useState<Prog | null>(null);
  const [roadProg, setRoadProg] = useState<Prog | null>(null);
  const [files, setFiles] = useState<Array<[string, Uint8Array]>>([]);
  const [summary, setSummary] = useState('No folder chosen.');
  const [running, setRunning] = useState(false);
  const push = async () => {
    const t0 = now();
    setProg({ stage: 'Building HANDHELD.MAP', pct: null, detail: mapLabel, kind: '' });
    try {
      const r = await ctx.run(() => ctx.client.pushAddressMap(mapText, (d, tot) => { const [p, txt] = progressText(d, tot, now() - t0); setProg({ stage: 'Writing HANDHELD.MAP', pct: p, detail: txt, kind: '' }); }));
      setProg({ stage: 'Done: address map pushed', pct: 100, detail: `${mapLabel}${r ? ` · ${r}` : ''}. The handheld uses it from the next power-up; relay frames need the hashes to match.`, kind: 'ok' });
    } catch (e) { setProg({ stage: 'Failed: HANDHELD.MAP not changed', pct: null, detail: (e as Error).message, kind: 'err' }); }
  };
  const choose = async (list: FileList | null) => {
    if (!list?.length) return;
    const raw: Record<string, Uint8Array> = {};
    for (const f of Array.from(list)) raw[f.name] = await readFileAsBytes(f);
    try {
      const checked = ctx.client.mapFolderFiles ? ctx.client.mapFolderFiles(raw) : Object.entries(raw).map(([n, d]) => [n.toUpperCase(), d] as [string, Uint8Array]);
      if (!checked.some(([n]) => n === 'MAP.IDX') || !checked.some(([n]) => n === 'MAP.DAT')) throw new Error('a map folder holds MAP.IDX and MAP.DAT');
      setFiles(checked);
      const total = checked.reduce((s, [, d]) => s + d.length, 0);
      setSummary(`OK  ${checked.map(([n, d]) => `${n} ${fmtBytes(d.length)}`).join(', ')}\ntotal ${fmtBytes(total)}`);
    } catch (e) { setFiles([]); setSummary(`NO  ${(e as Error).message}`); }
  };
  const upload = async () => {
    if (!files.length || running) return;
    setRunning(true);
    const t0 = now();
    let start: number | null = null;
    let tStart = now();
    const total = files.reduce((s, [, d]) => s + d.length, 0);
    try {
      await ctx.run(() => ctx.client.putMapFiles(files, (name, _held, _size, done, tot) => {
        if (start === null) { start = done; tStart = now(); }
        const el = now() - tStart, sofar = done - start;
        const left = sofar > 0 && el > 2 && done < tot ? ` · ~${fmtDur((el * (tot - done)) / sofar)} left` : '';
        const rate = sofar > 0 && el > 1 ? ` · ${(sofar / Math.max(el, 1e-6) / 1024).toFixed(0)} kB/s` : '';
        setRoadProg({ stage: `Uploading ${name}${start ? ` (resumed at ${fmtBytes(start)})` : ''}`, pct: tot ? (100 * done) / tot : 100,
          detail: `${(done / 1048576).toFixed(1)} of ${(tot / 1048576).toFixed(1)} MB · ${fmtDur(now() - t0)}${left}${rate}`, kind: '' });
      }));
      setRoadProg({ stage: 'Done: map uploaded', pct: 100, detail: `${fmtBytes(total)} in ${fmtDur(now() - t0)}`, kind: 'ok' });
    } catch (e) {
      const m = (e as Error).message;
      setRoadProg({ stage: m === 'cancelled' ? 'Stopped' : 'Failed', pct: null, detail: `${m}. Press Upload map to carry on from where it stopped.`, kind: 'err' });
    } finally { setRunning(false); }
  };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Card title="Address map → HANDHELD.MAP">
        <Note style={{ margin: 0 }}>Pushes the map in use ({mapLabel}) to the handheld, so it decodes the same channels and accepts relay frames (it checks the map hash).</Note>
        <div style={{ marginTop: 8 }}><Button kind="primary" disabled={!ctx.connected} onClick={() => void push()}>Push the address map</Button></div>
        {prog && <ProgressBlock {...prog} />}
      </Card>
      <Card title="Offline road map (MAP folder)">
        <input type="file" multiple aria-label="MAP folder files" onChange={(e) => void choose(e.target.files)} />
        <pre style={{ ...mono, fontSize: 13, whiteSpace: 'pre-wrap' }} data-testid="road-summary">{summary}</pre>
        <div style={{ display: 'flex', gap: 8 }}>
          <Button kind="primary" disabled={!ctx.connected || !files.length || running} onClick={() => void upload()}>Upload map</Button>
          <Button disabled={!running} onClick={() => ctx.client.cancel()}>Cancel</Button>
        </div>
        {roadProg && <ProgressBlock {...roadProg} />}
        <Note>Sent over the fast binary link, each file is checked before it replaces the old one, and an interrupted upload carries on from what the handheld already holds when you press Upload again.</Note>
      </Card>
    </div>
  );
}

function Console({ ctx, lines, clear, add }: { ctx: Ctx; lines: string[]; clear: () => void; add: (t: string) => void }) {
  const t = useTheme();
  const [line, setLine] = useState('');
  const [follow, setFollow] = useState(true);
  const box = useRef<HTMLPreElement>(null);
  useEffect(() => { if (follow && box.current) box.current.scrollTop = box.current.scrollHeight; }, [lines, follow]);
  const send = () => {
    const text = line.trim();
    if (!text || !ctx.connected) return;
    add(`> ${text}`);
    ctx.client.sendLine(text);
    setLine('');
  };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
        <Check checked={follow} onChange={setFollow}>Follow</Check>
        <Button onClick={clear}>Clear</Button>
        <Button onClick={() => downloadFile(`handheld-console-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')}.log`, lines.join('\n') + '\n', 'text/plain')}>Save to file</Button>
      </div>
      <pre ref={box} data-testid="cyd-console" style={{ ...mono, fontSize: 13, background: t.bg, border: `2px solid ${t.rule}`, height: 360, overflow: 'auto', margin: 0, padding: 8 }}>{lines.join('\n')}</pre>
      <div style={{ display: 'flex', gap: 8 }}>
        <TextInput aria-label="console line" value={line} onChange={setLine} onEnter={send} monospace disabled={!ctx.connected}
          placeholder="a line to send, e.g.  HL STAT   (it goes out as typed; replies appear above)" style={{ flex: 1 }} />
        <Button kind="primary" disabled={!ctx.connected} onClick={send}>Send</Button>
      </div>
    </div>
  );
}

function Install({ ctx, setFlashing }: { ctx: Ctx; setFlashing: (on: boolean) => void }) {
  const [image, setImage] = useState<{ name: string; data: Uint8Array } | null>(null);
  const [guard, setGuard] = useState('No image chosen.');
  const [baud, setBaud] = useState(String(DEFAULT_BAUD));
  const [running, setRunning] = useState(false);
  const [prog, setProg] = useState<Prog | null>(null);
  const choose = async (f: File | undefined) => {
    if (!f) return;
    const data = await readFileAsBytes(f);
    try {
      checkImage(data, f.name);
      setImage({ name: f.name, data });
      setGuard(`OK  ${f.name}: ${fmtBytes(data.length)}, merged image (partition table at 0x8000), target ${CHIP_NAME}`);
    } catch (e) { setImage(null); setGuard(`NO  ${(e as Error).message}`); }
  };
  const install = async () => {
    if (!image || !ctx.connected || running) return;
    if (!ctx.confirm('Write this image to the handheld now? Do not unplug it until it is done.')) return;
    setRunning(true); setFlashing(true);
    setProg({ stage: 'Starting', pct: null, detail: '', kind: '' });
    try {
      await ctx.client.flash(image.data, Number(baud), (p: FlashProgress) => {
        if (p.stage === 'Failed') { setProg({ stage: 'Failed', pct: null, detail: p.text, kind: 'err' }); return; }
        let detail = '';
        if (p.total) {
          detail = `${(p.written / 1048576).toFixed(2)} of ${(p.total / 1048576).toFixed(2)} MB · ${fmtDur(p.elapsed)}`;
          const e = eta(p);
          if (e) detail += ` · ~${fmtDur(e)} left`;
          detail += ' · do not unplug';
        }
        setProg({ stage: p.stage, pct: p.pct, detail: detail || p.text, kind: '' });
      });
      setProg({ stage: 'Done', pct: 100, detail: 'The board is running the new firmware and the tab is connected to it again.', kind: 'ok' });
    } catch (e) { setProg({ stage: 'Failed', pct: null, detail: (e as Error).message, kind: 'err' }); } finally { setRunning(false); setFlashing(false); }
  };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <input type="file" accept=".bin" aria-label="firmware image" onChange={(e) => void choose(e.target.files?.[0])} />
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <span>Speed</span>
        <Select aria-label="flash speed" value={baud} onChange={setBaud} options={BAUDS.map((b) => [String(b), `${b} baud`] as const)} />
        <Button kind="primary" disabled={!ctx.connected || !image || running} onClick={() => void install()}>Install</Button>
        <Button disabled={!running} onClick={() => ctx.client.cancel()}>Cancel</Button>
      </div>
      <pre style={{ ...mono, fontSize: 13, whiteSpace: 'pre-wrap', margin: 0 }} data-testid="install-guard">{guard}</pre>
      {prog && <ProgressBlock {...prog} />}
      <Note>The tab lets go of the serial port while the board is flashed and connects again once it has restarted. Only an ESP32-S3 is written: another chip is refused before anything is erased. If the board will not enter its bootloader, hold BOOT, tap RESET, release BOOT, and press Install again. Connect first (so the tab knows the port).</Note>
    </div>
  );
}

export function CydTab(props: { client: CydClient | null; mapText: string; mapLabel: string; confirm?: Confirm; ecuPort?: string;
  onHello?: (info: Record<string, string>) => void }) {
  const t = useTheme();
  const { client } = props;
  const confirm = props.confirm ?? ((text: string) => window.confirm(text));
  const [view, setView] = useState<View>('status');
  const [ports, setPorts] = useState<CydPortChoice[]>([]);
  const [port, setPort] = useState(() => { try { return localStorage.getItem('hondalog.cyd.port') ?? ''; } catch { return ''; } });
  const [state, setState] = useState<CydState>('disconnected');
  const [detail, setDetail] = useState('');
  const [info, setInfo] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);
  const [lines, setLines] = useState<string[]>([]);
  const add = (text: string) => setLines((l) => [...l.slice(-4999), `${new Date().toLocaleTimeString()}  ${text}`]);

  useEffect(() => {
    if (!client) return;
    const offC = client.onConsole(add);
    const offS = client.onState?.((s, d) => { setState(s); setDetail(d); if (s === 'disconnected') { setInfo({}); if (d) setMsg(`Handheld: ${d}`); } });
    void client.ports().then((p) => { setPorts(p); setPort((cur) => cur || p.find((x) => x.guess === 'cyd')?.id || p[0]?.id || ''); }).catch(() => undefined);
    return () => { offC(); offS?.(); };
  }, [client]);

  if (!client) {
    return <Card title="CYD black box"><Note style={{ color: t.fg }}>The CYD handheld module is not part of this build yet (src/core/cyd). Connect the handheld here once it is.</Note></Card>;
  }
  const connected = state === 'connected';
  const run = async <T,>(fn: () => Promise<T>): Promise<T> => { setBusy(true); try { return await fn(); } finally { setBusy(false); } };
  const ctx: Ctx = { client, connected, info, say: setMsg, confirm, busy, run };
  const toggle = async () => {
    if (connected) { await client.disconnect(); setState('disconnected'); setInfo({}); setDetail(''); return; }
    if (props.ecuPort && port && props.ecuPort === port) setMsg('The live connection has this port open: disconnect it there first.');
    try { localStorage.setItem('hondalog.cyd.port', port); } catch { /* no storage */ }
    setState('connecting'); setDetail(port);
    try {
      const hello = await client.connect(port);
      setInfo(hello); setState('connected'); setDetail(`${hello.id ?? ''} fw ${hello.fw ?? '?'}`);
      add(`connected: ${hello.id ?? '?'} fw ${hello.fw ?? '?'} (${hello.variant ?? '?'} build)`);
      props.onHello?.(hello);
      if (hello.variant === 'gps-p1') setMsg('This is the GPS-on-P1 build: it cannot listen.');
    } catch (e) { setState('disconnected'); setDetail(''); setMsg(`Handheld: ${(e as Error).message}`); }
  };
  const stateText = { connected: `Connected  ${detail}`, connecting: `Connecting  ${detail}`, flashing: 'Installing firmware', disconnected: 'Not connected' }[state];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minHeight: 0 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 700 }}>Port</span>
        <Select aria-label="CYD port" value={port} disabled={state !== 'disconnected'} onChange={setPort} style={{ minWidth: 320 }}
          options={(ports.length ? ports : [{ id: port, label: port || '(no port)' }]).map((p) => [p.id, p.label] as const)} />
        <Button disabled={state !== 'disconnected'} onClick={() => void client.ports().then(setPorts)}>Rescan</Button>
        <Button kind="primary" disabled={state === 'connecting' || state === 'flashing' || !port} onClick={() => void toggle()}>{connected ? 'Disconnect' : 'Connect'}</Button>
        <span data-testid="cyd-state" style={{ fontWeight: 700 }}>{stateText}</span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 0 }} role="tablist" aria-label="CYD views">
        {VIEWS.map(([k, label]) => (
          <Button key={k} active={view === k} onClick={() => setView(k)} style={{ borderRight: 'none' }}>{label}</Button>
        ))}
      </div>
      {msg && <Note style={{ color: t.fg }} >{msg}</Note>}
      <div style={{ minHeight: 0 }}>
        {view === 'status' && <Status ctx={ctx} />}
        {view === 'files' && <Files ctx={ctx} />}
        {view === 'config' && <Config ctx={ctx} />}
        {view === 'map' && <MapPush ctx={ctx} mapText={props.mapText} mapLabel={props.mapLabel} />}
        {view === 'console' && <Console ctx={ctx} lines={lines} clear={() => setLines([])} add={add} />}
        {view === 'install' && <Install ctx={ctx} setFlashing={(on) => setState(on ? 'flashing' : 'connected')} />}
      </div>
    </div>
  );
}
