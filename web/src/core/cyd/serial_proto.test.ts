import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { crc16, crc32, fit, fromHex, hex, utf8 } from './bytes';
import * as F from './format';
import * as P from './serial_proto';

const FIX = join(dirname(fileURLToPath(import.meta.url)), '__fixtures__');
const G = JSON.parse(readFileSync(join(FIX, 'frames.json'), 'utf-8'));

describe('frames are the Python bytes', () => {
  for (const f of G.frames) {
    it(`${f.name}: encode and decode`, () => {
      const payload = fromHex(f.payload);
      const frame = P.frameEncode(f.type, payload, f.seq);
      expect(hex(frame)).toBe(f.frame);
      const back = new P.Deframer().feed(frame);
      expect(back.length).toBe(1);
      expect([back[0]![0], back[0]![1], hex(back[0]![2])]).toEqual([f.type, f.seq, f.payload]);
    });
  }

  it('the deframer takes a stream cut anywhere and counts damage', () => {
    const all = G.frames.map((f: any) => fromHex(f.frame));
    const stream = new Uint8Array(all.reduce((n: number, a: Uint8Array) => n + a.length, 0));
    let pos = 0;
    for (const a of all) { stream.set(a, pos); pos += a.length; }
    for (const step of [1, 7, 97, stream.length]) {
      const d = new P.Deframer();
      const got: P.Frame[] = [];
      for (let i = 0; i < stream.length; i += step) got.push(...d.feed(stream.subarray(i, i + step)));
      expect(got.length).toBe(G.frames.length);
      expect(d.bad).toBe(0);
    }
    const broken = all[3].slice();
    broken[5] ^= 0x01;
    const d = new P.Deframer();
    expect(d.feed(broken)).toEqual([]);
    expect(d.bad).toBe(1);
  });

  it('crc16, crc32, cobs, fit, dtc', () => {
    for (const [s, c] of G.crc16) expect(crc16(utf8(s))).toBe(c);
    for (const [s, c] of G.crc32) expect(crc32(utf8(s))).toBe(c);
    for (const [raw, enc] of G.cobs) {
      expect(hex(P.cobsEncode(fromHex(raw)))).toBe(enc);
      expect(hex(P.cobsDecode(fromHex(enc))!)).toBe(raw);
    }
    expect(P.cobsDecode(Uint8Array.of(0x05, 1, 2))).toBeNull();
    for (const [t, n, want] of G.fit) expect(hex(fit(t, n))).toBe(want);
    for (const [c, t] of G.dtc) expect(F.dtcText(c)).toBe(t);
  });
});

describe('payloads', () => {
  const payload = (name: string) => fromHex(G.frames.find((f: any) => f.name === name).payload);

  it('hello packs and parses as the Python does', () => {
    const h = P.parseHello(payload('hello'))!;
    const want = G.hello;
    expect([h.rev, h.variant, h.caps, h.fw, h.device, h.mapHash, hex(h.mapSha), hex(h.ecuId)])
      .toEqual([want.rev, want.variant, want.caps, want.fw, want.device, want.map_hash, want.map_sha, want.ecu_id]);
    expect([h.linkState, h.backend, h.linkMode, h.nCh, h.flags, h.bootNo, h.baud, h.tUs, h.unixUs, h.helloMs, h.maxPayload])
      .toEqual([want.link_state, want.backend, want.link_mode, want.n_ch, want.flags, want.boot_no, want.baud, want.t_us,
        want.unix_us, want.hello_ms, want.max_payload]);
    expect(P.helloText(h)).toBe(G.hello_text);
    expect(hex(P.packHello(h))).toBe(hex(payload('hello')));
    expect(P.parseHello(payload('stats_old'))).toBeNull();
    const c = P.toCydHello(h);
    expect(c).toEqual({ id: 'HH-1373A0', fw: want.fw, model: 'cyd-4827s043n', variant: 'default', mapHash: '44979f9238b1',
      twoWay: true, caps: want.caps, boot: 7 });
  });

  it('stats, old and new shapes (test_hh_pps)', () => {
    expect(P.parseStats(payload('stats_old'))).toEqual(G.stats_old);
    expect(P.parseStats(payload('stats_new'))).toEqual(G.stats_new);
    expect(hex(P.packStats({ t_us: 5, unix_us: -1, reads: 100, errors: 2, hz_c: 1937, flags: 3, ring_peak: 99 })))
      .toBe(hex(payload('stats_old')));
    expect(hex(P.packStats({ t_us: 6, reads: 7, time_source: P.TS_PPS, time_err_us: 20 }))).toBe(hex(payload('stats_new')));
    expect(P.parseStats(new Uint8Array(10))).toBeNull();
  });

  it('relay frames are the spec bytes (test_hh_relay)', () => {
    expect([P.SR_RELAY_READ, P.SR_RELAY_ISO, P.SR_RELAY_TICK, P.SR_RELAY_LINK]).toEqual([0x50, 0x51, 0x52, 0x53]);
    expect([P.CAP_RELAY, P.CAP_HL, P.LINK_RELAY]).toEqual([64, 32, 5]);
    expect(hex(P.packRelayRead(0x0102030405060708n, 0x10, Uint8Array.of(0xaa, 0xbb, 0xcc), 1234)))
      .toBe('0807060504030201' + '10' + '03' + 'd204' + 'aabbcc');
    expect(hex(P.packRelayIso(1_000_000, 0x0c, Uint8Array.of(0x1a, 0xf8)))).toBe('40420f0000000000' + '41' + '0c' + '02' + '00' + '1af8');
    expect(hex(P.packRelayTick(5, 2500.4, 19.37, 77))).toBe(hex(payload('relay_tick')));
    expect(hex(P.packRelayTick(5, 1e9, 1e9, 2 ** 40))).toBe(hex(payload('relay_tick_clamped')));
    expect(hex(P.packRelayLink(9, P.LINK_ISO_ONLY, true, true, utf8('0123456789AB')))).toBe(hex(payload('relay_link')));
    expect(hex(P.packRelayLink(9, P.LINK_OFF, false, false))).toBe(hex(payload('relay_link_off')));
    const frames = new P.Deframer().feed(P.frameEncode(P.SR_RELAY_TICK, P.packRelayTick(5, 1, 2, 3), 7));
    expect(frames.length).toBe(1);
    expect([frames[0]![0], frames[0]![1]]).toEqual([P.SR_RELAY_TICK, 7]);
    expect(P.RELAY_TICK.unpack(frames[0]![2])).toEqual([5, 1, 200, 3]);
  });

  it('commands and answers', () => {
    expect(hex(P.TIME_RSP.pack(-5, 10, 30))).toBe(hex(payload('time_rsp')));
    expect(hex(P.ACK.pack(P.SC_MARK, 3, P.E_OFFSET, 4096))).toBe(hex(payload('ack')));
    expect(P.ACK.unpack(payload('ack'))).toEqual([P.SC_MARK, 3, P.E_OFFSET, 4096]);
    expect(hex(P.LIST.pack(P.F_CAPTURE, 5))).toBe(hex(payload('list')));
    expect(hex(P.READ_FILE.pack(P.F_DRIVE, 480, 12, 960))).toBe(hex(payload('read_file')));
    expect(hex(P.PUT_BEGIN.pack(P.F_MAP, 1234, 0xdeadbeef))).toBe(hex(payload('put_begin')));
    expect(hex(P.MAPUP_COMMIT.pack(P.MAPUP_FINAL))).toBe(hex(payload('mapup_commit')));
    const info = P.MAPUP_INFO.unpack(payload('mapup_info'));
    expect([info[0], info[1], info[2], info[3], F.cstr(info[4])]).toEqual([P.MU_PAUSED, 100, 200, 0xabcdef01, 'MAP/MAP.DAT']);
    expect(hex(P.TIME_REQ.pack(123456789012n))).toBe(hex(payload('time_req')));
  });

  it('events and GPS records', () => {
    expect(P.parseEvent(payload('event_float'))).toEqual({ tUs: 77, code: F.EV_STALL, src: 1, aux: 0, value: 412.5 });
    const m = P.parseEvent(payload('event_marker'))!;
    expect(m.value).toBe(3);
    expect(P.eventText(m)).toBe('MARK 3 (handheld button)');
    expect(P.eventText({ tUs: 0, code: F.EV_STALL, src: 2, aux: 0, value: 0 })).toBe('stall: link lost (no reply from ECU)');
    expect(P.eventText({ tUs: 0, code: F.EV_DTC, src: 0, aux: 0, value: 0x0301 })).toBe('trouble code P0301');
    const g = P.parseGps(payload('gps'))!;
    expect(g.gps_lat).toBeCloseTo(32.2746, 6);
    expect(g.gps_lon).toBeCloseTo(-90.132, 6);
    expect(g.gps_speed).toBeCloseTo(36, 6);
    expect([g.gps_heading, g.gps_sats, g.gps_fix, g.gps_hdop, g.gps_utc]).toEqual([90, 9, 1, 0.9, 1700000000]);
  });

  it('time text, map upload names, status text', () => {
    expect(P.timeText(P.TS_PPS, 20)).toBe('PPS ±20µs');
    expect(P.timeText(P.TS_PPS, 1500)).toBe('PPS ±1.5ms');
    expect(P.timeText(P.TS_NMEA, 0)).toBe('GPS ±1s');
    expect(P.timeText(P.TS_HOST, 0)).toBe('PC');
    expect(P.timeText(P.TS_NONE, 0)).toBe('none');
    for (const ok of ['MAP/MAP.IDX', 'MAP/MAP.DAT', 'MAP/README.TXT', 'MAP/A']) expect(P.mapupNameOk(ok)).toBe(true);
    for (const bad of ['MAP/UPLOAD.TMP', 'map/map.idx', 'MAP/../X', 'MAP/X/Y', 'HANDHELD.MAP', 'MAP/TOOLONGNAME.DAT', 'MAP/']) {
      expect(P.mapupNameOk(bad)).toBe(false);
    }
    expect(P.statusText(P.E_CRC)).toBe('checksum mismatch');
    expect(P.statusText(-99)).toBe('error -99');
  });
});
