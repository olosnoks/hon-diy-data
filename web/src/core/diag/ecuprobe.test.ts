/** ECU probe: the read-only whitelist and a full run on the fake ECU.
 *  Port of tests/test_ecuprobe.py (the CLI case has no browser counterpart;
 *  report files are tested through ProbeReport.files()). */

import { describe, expect, it } from 'vitest';
import { fromHex } from '../obd2/bytes';
import { VirtualClock } from '../obd2/clock';
import { FakeEcu, LoopbackTransport, type FakeEcuOptions } from '../obd2/testing/fakeEcu';
import * as ep from './ecuprobe';

function rig(o: FakeEcuOptions = {}) {
  const clock = new VirtualClock(1000);
  return { clock, transport: new LoopbackTransport(new FakeEcu(o), clock) };
}

function traceTx(r: ep.ProbeReport): Uint8Array[] {
  const out: Uint8Array[] = [];
  for (const line of r.trace.split('\n')) {
    const parts = line.trim().split(/\s+/);
    if (parts[1] === 'TX') out.push(fromHex(parts.slice(2).join(' ')));
  }
  return out;
}

describe('whitelist', () => {
  for (const sid of [0x04, 0x08, 0x11, 0x14, 0x27, 0x2e, 0x2f, 0x30, 0x31, 0x34, 0x35, 0x36, 0x37, 0x3b, 0x3d, 0x00, 0x3e, 0x85]) {
    it(`blocks service ${sid.toString(16)}`, () => {
      expect(() => ep.checkIso(Uint8Array.of(sid, 0))).toThrow(ep.ProbeBlocked);
    });
  }

  for (const p of ['1003', '1085', '1002', '10']) {
    it(`blocks non-default session ${p}`, () => {
      expect(() => ep.checkIso(fromHex(p))).toThrow(ep.ProbeBlocked);
    });
  }

  it('blocks an empty request', () => {
    expect(() => ep.checkIso(new Uint8Array(0))).toThrow(ep.ProbeBlocked);
  });

  for (const p of ['0100', '020c00', '03', '050101', '0600', '07', '0902', '1a80', '2101', '22f190', '2300000004', '1081']) {
    it(`allows read ${p}`, () => {
      expect(() => ep.checkIso(fromHex(p))).not.toThrow();
    });
  }

  it('every planned request is whitelisted', () => {
    for (const [, payloads] of ep.isoPlan()) for (const p of payloads) ep.checkIso(p);
    for (const c of ep.HONDA_CANDIDATES) ep.checkHonda(c);
    expect(ep.HONDA_CANDIDATES.length).toBe(24);
  });

  it('a Honda entry not marked read-only is refused and never sent', async () => {
    const { clock, transport } = rig();
    const bad: ep.HondaCandidate = { name: 'write?', baud: 9600, preDelayS: 0, wake: null, request: fromHex('30050002'), readOnly: false };
    expect(() => ep.checkHonda(bad)).toThrow(ep.ProbeBlocked);
    const r = await ep.runEcuProbe({
      transport, clock, candidates: [bad], plan: [['A bad', [Uint8Array.of(0x04), Uint8Array.of(0x2e, 0, 0)], false]],
    });
    expect(r.exchanges.map((e) => e.kind)).toEqual([ep.BLOCKED, ep.BLOCKED, ep.BLOCKED]);
    const sent = traceTx(r);
    expect(sent.some((d) => d[3] === 0x04 || d[3] === 0x2e)).toBe(false);
    expect(sent.some((d) => d[0] === 0x30 && d[1] === 0x05)).toBe(false);
  });

  it('Honda checksum is two\'s complement', () => {
    expect(Buffer.from(ep.hondaFrame(ep.HONDA_CANDIDATES[0]!)).toString('hex')).toBe('20050002d9');
    const frames = new Set(ep.HONDA_CANDIDATES.map((c) => Buffer.from(ep.hondaFrame(c)).toString('hex')));
    for (const h of ['20050010cb', '20051010bb', '20052010ab', '2005760a5b']) expect(frames.has(h)).toBe(true);
  });

  it('only Honda read commands are allowed', () => {
    for (const c of ep.HONDA_CANDIDATES) {
      ep.checkHonda(c);
      expect(c.request[0]).toBe(0x20);
    }
    const reset: ep.HondaCandidate = { name: 'reset', baud: 9600, preDelayS: 0, wake: null, request: fromHex('210401'), readOnly: true };
    expect(() => ep.checkHonda(reset)).toThrow(ep.ProbeBlocked);
  });
});

describe('full runs on the fake ECU', () => {
  for (const honda of [true, false]) {
    it(`finishes and reports (honda protocol ${honda})`, async () => {
      const { clock, transport } = rig({ hondaProtocol: honda, dtcs: ['P0135'] });
      const r = await ep.runEcuProbe({ transport, clock });
      expect(r.isoInit).toBeTruthy();
      expect(r.elapsedS).toBeLessThan(90);
      const byReq = new Map(r.exchanges.map((e) => [Buffer.from(e.request).toString('hex'), e]));
      expect(byReq.get('0100')!.kind).toBe(ep.POSITIVE);
      expect(byReq.get('0100')!.meaning).toContain('0C');
      expect(byReq.get('020c00')!.kind).toBe(ep.POSITIVE);
      expect(byReq.get('2100')!.kind).toBe(ep.SILENT);
      expect(byReq.get('050001')!.meaning).toBe('Mode 05 supported: 01 02 03 04 05');
      expect(r.exchanges.some((e) => e.kind === ep.BLOCKED)).toBe(false);
      // manufacturer stages stop early on silence
      expect(r.exchanges.filter((e) => e.request[0] === 0x21).length).toBe(ep.SILENT_STOP);
      expect(r.notes.some((n) => n.includes('0x21') && n.includes('stopped'))).toBe(true);
      const hondaRows = r.exchanges.filter((e) => e.stage.startsWith('B'));
      expect(hondaRows.length).toBe(ep.HONDA_CANDIDATES.length);
      // the P2P, as on the car: Honda reads answer only at 10400 baud after the wake frame
      const positive = ep.HONDA_CANDIDATES.filter((_, i) => hondaRows[i]!.kind === ep.POSITIVE).map((c) => c.name);
      const want = honda ? ep.HONDA_CANDIDATES.filter((c) => c.baud === 10400 && c.wake).map((c) => c.name) : [];
      expect(positive).toEqual(want);
      expect([0, 6]).toContain(want.length);
      const md = r.toMarkdown();
      expect(md).toContain('| stage | service | request | reply | meaning |');
      expect(md).toContain('## Byte trace');
      expect(md).toContain('BREAK');
      expect(md).toContain('TX    20 05 00 02 D9');
      // interesting hits first
      const table = md.split('\n').filter((l) => l.startsWith('| ')).slice(1);
      expect(table[0]).not.toContain('positive');
      expect(table[0]).not.toContain('no reply');
      const files = r.files();
      expect(files.md.startsWith('# hondalog ECU probe')).toBe(true);
      expect(files.hex.split('TX').length - 1).toBeGreaterThan(20);
    });
  }

  it('ISO first, then the Honda table after the session gap', async () => {
    const { clock, transport } = rig();
    const r = await ep.runEcuProbe({ transport, clock, hondaFirst: false });
    expect(r.isoInit).toBeTruthy();
    const firstB = r.exchanges.findIndex((e) => e.stage.startsWith('B'));
    expect(firstB).toBeGreaterThan(0);
    expect(r.exchanges.slice(firstB).filter((e) => e.kind === ep.POSITIVE).length).toBe(6);
  });

  it('records a failed init and still runs the Honda table', async () => {
    const { clock, transport } = rig({ iso: false });
    const r = await ep.runEcuProbe({ transport, clock });
    expect(r.isoInit).toBe('');
    expect(r.notes.some((n) => n.includes('5-baud init failed'))).toBe(true);
    expect(r.toMarkdown()).toContain('ISO 9141-2 init: failed');
  });
});
