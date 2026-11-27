/** First-contact run, link probes, adapter detection and the byte trace.
 *  Port of tests/test_firstcontact.py (the sysfs/udev and CLI cases have no
 *  browser counterpart; the adapter cases test the USB-id detection instead). */

import { describe, expect, it } from 'vitest';
import type { Transport } from '../types';
import { VirtualClock } from '../obd2/clock';
import { FakeEcu, LoopbackTransport, P2T_FRAMING, type FakeEcuOptions } from '../obd2/testing/fakeEcu';
import { detectAdapter } from './adapter';
import * as fc from './firstcontact';
import { DEFAULT_FRAMING, P2P_FRAMING, SimpleHondaLink, framingFromMap } from './honda';
import { probeBlockLength, probeTiming, sweep, timingOk } from './probe';
import { TracingTransport } from './trace';

function rig(o: FakeEcuOptions = {}) {
  const clock = new VirtualClock(1000);
  const ecu = new FakeEcu(o);
  return { clock, ecu, transport: new LoopbackTransport(ecu, clock) };
}

async function run(setup?: (e: FakeEcu) => void, o: FakeEcuOptions = {}) {
  const { clock, ecu, transport } = rig(o);
  setup?.(ecu);
  return fc.runFirstContact({ transport, clock, listenS: 0.5, platform: 'linux' });
}

// ---- the verdicts ----------------------------------------------------------

describe('verdicts', () => {
  it('Honda protocol OK with everything measured', async () => {
    const r = await run(undefined, { dtcs: ['P0135'] });
    expect(r.verdict).toBe(fc.HONDA_OK);
    expect(r.echo).toBe(true);
    expect(r.hondaNoInit).toBe(false);
    expect(r.hondaWithInit).toBe(true); // a P2P wants the wake frame
    expect(r.summary).toContain('only after the wake-up bytes');
    expect(new TextDecoder().decode(r.ecuId)).toMatch(/^SIM-P2P/);
    expect(r.blockProbe!.maxReadLen).toBe(64);
    expect(timingOk(r.timing!)).toBe(true);
    expect(r.timing!.turnaroundMedianMs).toBeGreaterThan(1.5);
    expect(r.timing!.turnaroundMedianMs).toBeLessThan(4.5); // sim: 2 ms + 1 ms USB tick
    expect(r.sweep!.answered).toBe(256);
    expect(r.isoOk).toBe(true);
    expect(r.obd2Text).toContain('P0135');
    expect(r.recommended['max_read_len']).toBe(64);
    const md = r.toMarkdown();
    expect(md).toContain('**verdict: Honda protocol OK**');
    expect(md).toContain('## Byte trace');
    expect(md).toContain('TX    20 05 00 02 D9');
    expect(md).toContain('BREAK'); // the 5-baud init is in the trace
    expect(md).toContain('max_read_len = 64');
    const files = r.files();
    expect(files.mdName).toMatch(/^firstcontact-\d{8}-\d{6}\.md$/);
    expect(files.hex.startsWith('# hondalog first contact')).toBe(true);
    expect(files.hex).toContain('TX    20 05 00 02 D9');
  });

  it('ISO only', async () => {
    const r = await run(undefined, { hondaProtocol: false });
    expect(r.verdict).toBe(fc.ISO_ONLY);
    expect(r.echo).toBe(true);
    expect(r.hondaReply.length).toBe(0);
    expect(r.isoOk).toBe(true);
    const names = r.steps.map((s) => s.name);
    expect(names).toContain('Honda read 0x00 (after wake-up)'); // the wake bytes were tried
    expect(names).toContain('alternate framings');
    expect(r.sweep).toBeNull();
  });

  it('cable dead', async () => {
    const r = await run((e) => (e.cableDead = true));
    expect(r.verdict).toBe(fc.CABLE_DEAD);
    expect(r.echo).toBe(false);
    expect(r.steps.some((s) => s.name === 'ISO 9141-2 init' && s.ok === null)).toBe(true);
    const md = r.toMarkdown();
    expect(md).toContain('## Next steps');
    expect(md).toContain('pin 16');
  });

  it('ECU silent', async () => {
    const r = await run((e) => e.powerCut());
    expect(r.verdict).toBe(fc.ECU_SILENT);
    expect(r.echo).toBe(true);
    expect(r.hondaReply.length).toBe(0);
    expect(r.isoOk).toBe(false);
  });

  it('no port', async () => {
    const { clock, transport } = rig();
    transport.failOpen = true;
    const r = await fc.runFirstContact({ transport, clock, listenS: 0, platform: 'linux' });
    expect(r.verdict).toBe(fc.NO_PORT);
    expect(r.summary).toContain('dialout');
    const w = await fc.runFirstContact({ transport, clock, listenS: 0, platform: 'windows' });
    expect(w.summary).toContain('port free');
    expect(w.toMarkdown()).toContain('Device Manager');
  });

  it('port lost during the run is reported, not thrown', async () => {
    const { clock, transport } = rig();
    const orig = transport.write.bind(transport);
    let n = 0;
    transport.write = async (d: Uint8Array) => {
      if (++n > 3) throw new Error('device lost');
      return orig(d);
    };
    const r = await fc.runFirstContact({ transport, clock, listenS: 0, platform: 'linux' });
    expect(r.verdict).toBe(fc.NO_PORT);
    expect(r.summary).toContain('Cable pulled?');
  });

  it('finds an alternate framing', async () => {
    // An ECU whose length byte is the data length (no +3): the default parser
    // rejects it, an alternate framing accepts it.
    const { clock, ecu, transport } = rig();
    ecu.framing = { ...P2T_FRAMING, replyLenExtra: 0 };
    const r = await fc.runFirstContact({
      transport, clock, listenS: 0, iso: false, framing: { ...P2P_FRAMING, baud: 9600 }, platform: 'linux',
    });
    expect(r.verdict).toBe(fc.HONDA_OK);
    expect(r.hondaFraming).toBe('reply length byte = data length');
    expect(r.recommended['reply_len_extra']).toBe(0);
    expect(r.toMarkdown()).toContain('reply_header = -1');
  });

  it('quick skips the slow steps', async () => {
    const { clock, transport } = rig();
    const r = await fc.runFirstContact({ transport, clock, listenS: 0.2, quick: true, iso: false, platform: 'linux' });
    expect(r.timing).toBeNull();
    expect(r.sweep).toBeNull();
    expect(r.steps.some((s) => s.name === 'timing probe' && s.ok === null)).toBe(true);
  });

  it('a P2T answers without the wake frame', async () => {
    const { clock, transport } = rig({ framing: P2T_FRAMING });
    const r = await fc.runFirstContact({ transport, clock, listenS: 0, iso: false, quick: true, framing: DEFAULT_FRAMING, platform: 'linux' });
    expect(r.verdict).toBe(fc.HONDA_OK);
    expect(r.hondaNoInit).toBe(true);
    expect(r.summary).toContain('without the wake-up');
  });

  it('takes the framing from an address map', () => {
    const f = framingFromMap({ protocol: { baud: 9600, init_sequence: '', reply_header: -1 } as never });
    expect(f.baud).toBe(9600);
    expect(f.initSequence.length).toBe(0);
    expect(f.replyHeader).toBe(-1);
    expect(framingFromMap(null).baud).toBe(10400);
  });
});

// ---- probes ----------------------------------------------------------------

async function p2pLink(transport: LoopbackTransport, clock: VirtualClock) {
  const link = new SimpleHondaLink(transport, P2P_FRAMING, clock);
  await link.wake();
  return link;
}

describe('probes', () => {
  it('block length probe finds the ECU limit', async () => {
    const { clock, transport } = rig({ maxReadLen: 16 });
    const p = await probeBlockLength(await p2pLink(transport, clock));
    expect(p.maxReadLen).toBe(16);
    expect(p.results.get(16)).toBe(true);
    expect(p.results.get(32)).toBe(false);
  });

  it('timing probe measures a slow ECU and recommends', async () => {
    const { clock, transport } = rig({ turnaroundS: 0.01 });
    const link = await p2pLink(transport, clock);
    const p = await probeTiming(link, { reads: 50 });
    expect(p.reads).toBe(50);
    expect(p.failed).toBe(0);
    expect(p.turnaroundMedianMs).toBeGreaterThan(9.5);
    expect(p.turnaroundMedianMs).toBeLessThan(12.5);
    expect(p.recommended['reply_timeout_s']).toBeGreaterThanOrEqual(0.05);
    expect(p.recommended['inter_request_delay_s']).toBe(0.001);
    expect(link.gap).toBe(P2P_FRAMING.interRequestDelayS);
    expect(link.gapFloor).toBe(P2P_FRAMING.interRequestDelayS);
  });

  it('timing probe apply sets the link limits', async () => {
    const { clock, transport } = rig({ turnaroundS: 0.01 });
    const link = await p2pLink(transport, clock);
    const p = await probeTiming(link, { reads: 30, apply: true });
    expect(link.gapFloor).toBe(p.recommended['inter_request_delay_s']);
    expect(link.replyTimeoutS).toBe(p.recommended['reply_timeout_s']);
  });

  it('sweep marks dead addresses byte by byte', async () => {
    const { clock, ecu, transport } = rig();
    ecu.hondaHook = (addr, length) => (addr <= 0x42 && 0x42 < addr + length ? null : undefined);
    const s = await sweep(await p2pLink(transport, clock));
    expect(s.answered).toBe(255);
    expect(s.unanswered).toEqual([[0x42, 1]]);
    expect(s.hexdump().split('\n')[5]).toContain('--');
    expect(s.asciiRuns().some(([, t]) => t.includes('SIM-P2P'))).toBe(true);
  });
});

// ---- trace -----------------------------------------------------------------

describe('trace', () => {
  it('records and merges', async () => {
    const { clock, transport } = rig({ framing: P2T_FRAMING }); // P2T: no wake bytes in the trace
    const tr = new TracingTransport(transport, clock);
    const link = new SimpleHondaLink(tr, DEFAULT_FRAMING, clock);
    await link.readBlock(0, 2);
    expect(tr.bytesSent()).toBe(5);
    expect(tr.bytesReceived()).toBe(10);
    const lines = tr.dump().trimEnd().split('\n').map((l) => l.trim().split(/\s+/));
    expect(lines[0]!.slice(1)).toEqual(['TX', '20', '05', '00', '02', 'D9']);
    // the echo and the reply (2 ms later) each read as one line
    expect(lines.map((l) => l[1])).toEqual(['TX', 'RX', 'RX']);
    expect(lines[1]!.slice(2)).toEqual(lines[0]!.slice(2));
    expect(lines[2]!.slice(2, 4)).toEqual(['00', '05']);
  });
});

// ---- adapter ---------------------------------------------------------------

describe('adapter', () => {
  const t = (kind: Transport['kind'], usb?: { vendorId: number; productId: number }) =>
    ({ kind, label: 'COM21', usb }) as Transport;

  it('FTDI by USB id', () => {
    const a = detectAdapter(t('webserial', { vendorId: 0x0403, productId: 0x6001 }));
    expect(a.isFtdi).toBe(true);
    expect(a.chip).toBe('FT232R');
    expect(a.kind).toBe('usb-serial');
    expect(a.verdict).toBe('unknown');
    expect(a.notes.join(' ')).toContain('latency timer');
  });

  it('CH340 is flagged as the handheld', () => {
    const a = detectAdapter(t('tauri', { vendorId: 0x1a86, productId: 0x7523 }));
    expect(a.isFtdi).toBe(false);
    expect(a.chip).toBe('CH340');
    expect(a.verdict).toBe('not-ftdi');
    expect(a.notes[0]).toContain('CYD');
  });

  it('simulator and unknown', () => {
    expect(detectAdapter(t('sim')).kind).toBe('simulator');
    expect(detectAdapter(t('webserial')).kind).toBe('serial');
  });

  it('report asks for the latency timer on Windows', async () => {
    const { clock, transport } = rig();
    Object.defineProperty(transport, 'kind', { value: 'webserial' });
    Object.defineProperty(transport, 'usb', { value: { vendorId: 0x0403, productId: 0x6001 } });
    const r = await fc.runFirstContact({ transport, clock, listenS: 0, iso: false, quick: true, platform: 'windows' });
    const md = r.toMarkdown();
    expect(md).toContain('Latency Timer (msec) = 1');
    expect(r.steps.find((s) => s.name === 'latency timer 1 ms')!.ok).toBeNull();
  });
});
