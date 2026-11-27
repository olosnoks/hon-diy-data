// Port of tests/test_protocol.py, plus golden frames from the Python code.
import { describe, expect, it } from 'vitest';
import * as p from './protocol';
import golden from '../sim/golden.json';

const hex = (s: string) => p.parseHex(s);
const toHex = (b: Uint8Array) => Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
const b = (...v: number[]) => Uint8Array.from(v);

describe('golden frames (byte-identical to hondalog/protocol.py)', () => {
  it.each(golden.requests as [number, number, string][])('request %i+%i', (addr, len, want) => {
    expect(toHex(p.buildReadRequest(addr, len))).toBe(want);
  });
  it.each(golden.replies as [string, string][])('reply for %s', (data, want) => {
    expect(toHex(p.buildReply(hex(data)))).toBe(want);
  });
});

describe('checksum and requests', () => {
  it('matches the known kerpz request', () => {
    expect(p.checksum(b(0x20, 0x05, 0x00, 0x10))).toBe(0xcb);
    expect(toHex(p.buildReadRequest(0x00, 0x10))).toBe('20050010cb');
  });
  it.each([[0x10, 0x10, '20051010bb'], [0x20, 0x10, '20052010ab'], [0x76, 0x0a, '2005760a5b']] as const)(
    'other known requests %i', (addr, len, want) => {
      expect(toHex(p.buildReadRequest(addr, len))).toBe(want);
    });
  it('every frame sums to zero', () => {
    for (let addr = 0; addr < 256; addr += 7) {
      for (const len of [1, 2, 16]) expect(p.verify(p.buildReadRequest(addr, len))).toBe(true);
    }
  });
  it('checksum wraps', () => {
    expect(p.checksum(b())).toBe(0);
    expect(p.checksum(b(0xff, 0x01))).toBe(0);
    expect(p.checksum(b(0xff, 0xff))).toBe(0x02);
    expect(p.verify(p.withChecksum(Array.from({ length: 200 }, (_, i) => i)))).toBe(true);
    expect(p.verify(b())).toBe(false);
  });
  it('range checks', () => {
    expect(() => p.buildReadRequest(0x100, 1)).toThrow();
    expect(() => p.buildReadRequest(0, 0)).toThrow();
  });
  it('reply framing', () => {
    const reply = p.buildReply(b(0x0b, 0xb8));
    expect(reply[0]).toBe(0x00);
    expect(reply[1]).toBe(5);
    expect(Array.from(reply.slice(2, 4))).toEqual([0x0b, 0xb8]);
    expect(p.verify(reply)).toBe(true);
    expect(reply.length).toBe(p.replySize(2));
  });
});

describe('echo stripping', () => {
  const req = p.buildReadRequest(0, 2);
  it('present', () => {
    const [rest, seen] = p.stripEcho(p.concat(req, [0, 5]), req);
    expect(seen).toBe(true);
    expect(Array.from(rest)).toEqual([0, 5]);
  });
  it('absent', () => {
    const [rest, seen] = p.stripEcho(b(0, 5, 1), req);
    expect(seen).toBe(false);
    expect(Array.from(rest)).toEqual([0, 5, 1]);
  });
  it('after junk', () => {
    const [rest, seen] = p.stripEcho(p.concat([0xff, 0], req, [0xaa]), req);
    expect(seen).toBe(true);
    expect(Array.from(rest)).toEqual([0xaa]);
  });
  it('echo only', () => {
    const [rest, seen] = p.stripEcho(req, req);
    expect(seen).toBe(true);
    expect(rest.length).toBe(0);
  });
});

describe('reply parsing', () => {
  const DATA = b(0x0b, 0xb8, 0x00, 0x11);
  const REQ = p.buildReadRequest(0, DATA.length);
  const REPLY = p.buildReply(DATA);
  const parse = (buf: Uint8Array, req = REQ, n = DATA.length, f?: p.FullFraming) => Array.from(p.parseReply(buf, req, n, f));

  it('with echo', () => expect(parse(p.concat(REQ, REPLY))).toEqual(Array.from(DATA)));
  it('without echo', () => expect(parse(REPLY)).toEqual(Array.from(DATA)));
  it('junk before echo and before frame', () => {
    expect(parse(p.concat([0xfe, 0], REQ, [0x55, 0, 0x99], REPLY, [1]))).toEqual(Array.from(DATA));
  });
  it('reply containing zero data', () => {
    const req = p.buildReadRequest(0x40, 16);
    expect(parse(p.concat(req, p.buildReply(new Uint8Array(16))), req, 16)).toEqual(Array(16).fill(0));
  });
  it('data that looks like a header', () => {
    const data = b(0, 7, 0, 7);
    const req = p.buildReadRequest(0, 4);
    expect(parse(p.concat(req, p.buildReply(data)), req, 4)).toEqual([0, 7, 0, 7]);
  });
  it('nothing received', () => {
    expect(() => parse(b())).toThrow(p.NoReply);
    expect(() => parse(b())).toThrow(/no bytes/);
  });
  it('echo only', () => expect(() => parse(REQ)).toThrow(/echo only/));
  it('truncated reply', () => expect(() => parse(p.concat(REQ, REPLY.slice(0, -2)))).toThrow(p.ShortReply));
  it('bad checksum', () => {
    const bad = REPLY.slice();
    bad[3] = bad[3]! ^ 0x40;
    expect(() => parse(p.concat(REQ, bad))).toThrow(p.BadChecksum);
  });
  it('wrong length byte', () => {
    const bad = REPLY.slice();
    bad[1] = bad[1]! + 1;
    bad[bad.length - 1] = p.checksum(bad.slice(0, -1));
    expect(() => parse(p.concat(REQ, bad))).toThrow(p.ProtocolError);
  });
  it('pure garbage', () => {
    expect(() => parse(p.concat(REQ, [0xff, 0xfe, 0xfd, 0xfc, 0xfb, 0xfa, 0xf9]))).toThrow(p.BadFrame);
  });
  it('all errors share a base class and carry their kind', () => {
    for (const E of [p.NoReply, p.ShortReply, p.BadChecksum, p.BadFrame]) {
      const e = new E('x');
      expect(e).toBeInstanceOf(p.ProtocolError);
      expect(e.kind).toBe(e.name);
    }
  });
  it('custom framing', () => {
    const f = p.withOverrides(p.DEFAULT_FRAMING, { reply_header: -1, cmd_read: 0x21 });
    const req = p.buildReadRequest(0, 2, f);
    expect(req[0]).toBe(0x21);
    const reply = p.withChecksum([0x7e, 5, 0xaa, 0xbb]);
    expect(parse(p.concat(req, reply), req, 2, f)).toEqual([0xaa, 0xbb]);
    expect(() => parse(p.concat(req, reply), req, 2, p.DEFAULT_FRAMING)).toThrow(p.ProtocolError);
  });
});

describe('framing overrides and hex', () => {
  it('rejects unknown keys', () => {
    expect(() => p.withOverrides(p.DEFAULT_FRAMING, { bogus: 1 })).toThrow();
  });
  it('init_sequence from a string, camelCase or snake_case', () => {
    expect(p.initBytes(p.withOverrides(p.DEFAULT_FRAMING, { init_sequence: '' })).length).toBe(0);
    expect(Array.from(p.initBytes(p.withOverrides(p.DEFAULT_FRAMING, { initSequence: '68 6a' })))).toEqual([0x68, 0x6a]);
    expect(toHex(p.initBytes(p.DEFAULT_FRAMING))).toBe('686af5afbfb3b2c1dbb3e9');
    expect(p.withOverrides(p.DEFAULT_FRAMING, { baud: 10400, retries: 0 })).toMatchObject({ baud: 10400, retries: 0, cmdRead: 0x20 });
  });
  it('find any frame', () => {
    const REPLY = p.buildReply(b(0x0b, 0xb8, 0, 0x11));
    expect(Array.from(p.findAnyFrame(p.concat([0x99], REPLY, [1]))!)).toEqual(Array.from(REPLY));
    expect(p.findAnyFrame(b(1, 2, 3))).toBeNull();
  });
  it('parse hex forms', () => {
    expect(Array.from(p.parseHex('20 05 00 10'))).toEqual([0x20, 5, 0, 0x10]);
    expect(Array.from(p.parseHex('20050010'))).toEqual([0x20, 5, 0, 0x10]);
    expect(Array.from(p.parseHex('0x20, 0x5, 0, 10'))).toEqual([0x20, 5, 0, 0x10]);
    expect(p.parseHex('').length).toBe(0);
    expect(() => p.parseHex('zz')).toThrow();
    expect(p.fmtHex(b(0, 0xab))).toBe('00 AB');
  });
});
