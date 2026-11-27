// Port of tests/test_addressmap.py and tests/test_addressmap_v2.py.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  AddressMap, Channel, compileFormula, DEFAULT_MAP_TEXT, greedyBlocks, loadDefault, loads, MapError,
} from './index';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = join(HERE, '..', '..', '..', '..');
const lf = (s: string) => s.replace(/\r\n/g, '\n');

function ch(name: string, addr: number, extra: Record<string, unknown> = {}): Channel {
  const entry: Record<string, unknown> = { name, addr: BigInt(addr) };
  for (const [k, v] of Object.entries(extra)) entry[k] = typeof v === 'number' && Number.isInteger(v) ? BigInt(v) : v;
  return new Channel(entry);
}

function mem256(fill: (a: number) => number): Map<number, number> {
  return new Map(Array.from({ length: 256 }, (_, a) => [a, fill(a)] as const));
}

// ---------------------------------------------------------------- test_addressmap.py

describe('address map (schema 1 tests)', () => {
  it('the embedded default is the shipped hondalog/data/addressmap.toml', () => {
    expect(DEFAULT_MAP_TEXT).toBe(lf(readFileSync(join(REPO, 'hondalog', 'data', 'addressmap.toml'), 'utf-8')));
  });

  it('default map loads with the P2P findings verified', () => {
    const m = loadDefault();
    expect(m.version).toBeTruthy();
    for (const name of ['rpm', 'ect', 'iat', 'map', 'tps', 'batt', 'o2', 'vss', 'ign', 'inj', 'stft', 'ltft']) {
      expect(m.channel(name), name).toBeDefined();
    }
    expect(m.channels.some((c) => c.kind === 'flag')).toBe(true);
    const verified = new Set(['rpm', 'vss', 'ect', 'iat', 'map', 'tps', 'stft', 'ltft', 'ac_sw', 'mil',
      'closed_loop', 'fuel_pump', 'ac_clutch', 'starter']);
    expect(new Set(m.channels.filter((c) => c.verified).map((c) => c.name))).toEqual(verified);
    for (const name of verified) {
      const ev = m.channel(name)!.evidence;
      expect(ev && ev.result, name).toBe('pass');
    }
    for (const name of ['vtec', 'fan', 'brake', 'baro', 'o2', 'batt', 'inj', 'ign', 'iacv']) {
      expect(m.channel(name)!.verified, name).toBe(false);
    }
  });

  it('blocks cover every channel within limits', () => {
    const m = loadDefault();
    const blocks = m.blocks();
    const covered = new Set(blocks.flatMap(([a, n]) => Array.from({ length: n }, (_, i) => a + i)));
    for (const c of m.channels) for (let a = c.addr; a < c.addr + c.length; a++) expect(covered.has(a)).toBe(true);
    expect(blocks.every(([, n]) => n >= 1 && n <= 16)).toBe(true);
    expect(blocks.length).toBeLessThanOrEqual(4);
  });

  it('plan_blocks splits and merges', () => {
    const c = [ch('a', 0x00, { length: 2 }), ch('b', 0x0f), ch('c', 0x10), ch('d', 0x80)];
    expect(greedyBlocks(c, 16)).toEqual([[0x00, 16], [0x10, 1], [0x80, 1]]);
  });

  it('decodes formulas', () => {
    const m = loadDefault();
    const mem = mem256(() => 0);
    mem.set(0x00, 0x0b);
    mem.set(0x01, 0xb8);
    mem.set(0x17, 146);
    mem.set(0x0f, 0x01);
    const v = m.decode(mem);
    expect(v.rpm).toBe(750);
    expect(v.batt).toBeCloseTo(13.97, 2);
    expect(v.closed_loop).toBe(1);
    expect(v.mil).toBe(0);
  });

  it('missing bytes decode to null', () => {
    const v = loadDefault().decode(new Map([[0, 1], [1, 2]]));
    expect(v.rpm).not.toBeNull();
    expect(v.ect).toBeNull();
    // arrays work too, with negative = unread
    const arr = new Array(256).fill(-1);
    arr[0] = 1;
    arr[1] = 2;
    expect(loadDefault().decode(arr).ect).toBeNull();
  });

  it('formula helpers', () => {
    expect(compileFormula('bit(x, 3)')(0x08, [8])).toBe(1);
    expect(compileFormula('signed(x, 8)')(0xff, [255])).toBe(-1);
    expect(compileFormula('b[0] * 256 + b[1]')(0, [1, 2])).toBe(258);
    expect(compileFormula('poly(x, 1, 2, 3)')(2, [2])).toBe(17);
    expect(compileFormula('x / 4 if x > 0 else 0')(8, [8])).toBe(2);
  });

  it.each(["__import__('os').system('true')", 'x.real', "open('/etc/passwd')", 'lambda: 1', "'a' * 3",
    'x +', 'y + 1', '[i for i in b]'])('formula rejects anything but arithmetic: %s', (bad) => {
    expect(() => compileFormula(bad)).toThrow(MapError);
  });

  it('a runtime error in a formula gives null', () => {
    expect(ch('z', 0, { formula: '1 / x' }).convert([0])).toBeNull();
  });

  it('reports bad files', () => {
    expect(() => loads("version = 'x'\n")).toThrow(/no .*channel/);
    expect(() => loads("[[channel]]\nname='a'\naddr=1\n[[channel]]\nname='a'\naddr=2\n")).toThrow(/duplicate/);
    expect(() => loads("[[channel]]\nname='a'\naddr=300\n")).toThrow(MapError);
    expect(() => loads("[[channel]]\nname='a'\naddr=1\nbogus=2\n")).toThrow(MapError);
    expect(() => loads('this is not toml [')).toThrow(MapError);
  });

  it('protocol overrides from the map', () => {
    const m = loads("[protocol]\nreply_timeout_s = 0.5\ninit_sequence = ''\n[[channel]]\nname='a'\naddr=1\n");
    expect(m.framing.replyTimeoutS).toBe(0.5);
    expect(m.framing.initSequence).toBe('');
    expect(loadDefault().framing.baud).toBe(10400);
    expect(loadDefault('P2T').framing.baud).toBe(9600);
    expect(loadDefault().framing.initSequence).toBe('686af5afbfb3b2c1dbb3e9');
    expect(loads("[protocol]\ninit_sequence = '68 6A,0xF5'\n[[channel]]\nname='a'\naddr=1\n").framing.initSequence).toBe('686af5');
    expect(() => loads("[protocol]\nwarp = 1\n[[channel]]\nname='a'\naddr=1\n"))
      .toThrow('address map: [protocol] unknown protocol setting: warp');
  });
});

// ---------------------------------------------------------------- test_addressmap_v2.py

const V1_MAP = `
version = "old"
ecu = "P2P"
[[channel]]
name = "rpm"
addr = 0x00
length = 2
formula = "1875000 / (x + 1)"
status = "verified"
[[channel]]
name = "ect"
addr = 0x10
formula = "x - 40"
`;

const V2_MAP = `
schema = 2
version = "test-1"
ecu_variant = "P2P"
[variants.P2P]
description = "target"
[variants.P28]
description = "obd1"
constants = { k = 2 }
[constants]
k = 1
[[channel]]
name = "rpm"
addr = 0x00
length = 2
formula = "x"
endian = "little"
rate = "fast"
status = "verified"
source = "test"
[channel.evidence]
method = "guided:idle"
summary = "agreed with tach"
[channel.variants.P28]
formula = "1875000 / (x + 1)"
[[channel]]
name = "ect"
addr = 0x10
formula = "x - 40"
rate = "slow"
[channel.variants.P28]
absent = true
[[math]]
name = "rpm_k"
expr = "[rpm] * k"
`;

describe('address map schema 2', () => {
  it('a v1 file loads unchanged', () => {
    const amap = loads(V1_MAP);
    expect(amap.schema).toBe(1);
    expect(amap.names).toEqual(['rpm', 'ect']);
    expect(amap.channel('rpm')!.verified).toBe(true);
    expect(amap.channel('rpm')!.rate).toBe('fast');
    expect(amap.channel('ect')!.rate).toBe('slow');
    expect(amap.channel('rpm')!.endian).toBe('big');
    const v = amap.decode(new Map([[0, 0x01], [1, 0x00], [0x10, 120]]));
    expect(v.rpm).toBeCloseTo(1875000 / 257, 9);
    expect(v.ect).toBe(80);
    expect(amap.math).toEqual([]);
    expect(amap.alarms).toEqual([]);
  });

  it('a v1 file with unknown top keys still loads', () => {
    expect(loads('custom = 1\n' + V1_MAP).names).toEqual(['rpm', 'ect']);
  });

  it('the shipped v1 default (0.1.0) still parses', () => {
    const old = loads(readFileSync(join(REPO, 'tests', 'data_v1_addressmap.toml'), 'utf-8'));
    const now = loadDefault();
    expect(old.schema).toBe(1);
    expect(now.names.slice(0, old.names.length)).toEqual(old.names);
    expect(now.names.slice(old.names.length)).toEqual(['fuel_pump', 'fan', 'ac_clutch', 'brake', 'starter']);
    const mem = mem256((a) => (a * 37 + 11) & 0xff);
    const changed = new Set(['tps', 'ac_sw', 'vtec']);
    const a = old.decode(mem);
    const b = now.decode(mem);
    for (const k of Object.keys(a)) {
      if (changed.has(k)) continue;
      if (a[k] === null) expect(b[k]).toBeNull();
      else expect(b[k]).toBeCloseTo(a[k]!, 9);
    }
    expect(b.tps).toBeCloseTo(mem.get(0x14)! / 2.55, 9);
    expect(a.tps).toBeCloseTo((mem.get(0x14)! - 24) / 2, 9);
    expect(b.ac_sw).toBe((mem.get(0x08)! >> 1) & 1);
    expect(a.ac_sw).toBe((mem.get(0x08)! >> 2) & 1);
    expect(b.vtec).toBe((mem.get(0x0a)! >> 2) & 1);
    expect(a.vtec).toBe((mem.get(0x0a)! >> 3) & 1);
  });

  it('v2 fields, variants and constants', () => {
    const amap = loads(V2_MAP);
    expect([amap.schema, amap.variant, amap.baseVariant]).toEqual([2, 'P2P', 'P2P']);
    const rpm = amap.channel('rpm')!;
    expect([rpm.endian, rpm.rate, rpm.source]).toEqual(['little', 'fast', 'test']);
    expect(rpm.evidence?.method).toBe('guided:idle');
    expect(amap.decode(new Map([[0, 0x01], [1, 0x02], [0x10, 50]])).rpm).toBe(0x0201);
    expect(amap.byRate().slow).toEqual([amap.channel('ect')]);
    expect(amap.mathCheck().compiled.get('rpm_k')!.eval({ rpm: 100, ect: 1 })).toBe(100);

    const p28 = loads(V2_MAP, 'P28');
    expect(p28.variant).toBe('P28');
    expect(p28.names).toEqual(['rpm']);
    expect(p28.channel('rpm')!.formula).toBe('1875000 / (x + 1)');
    expect(p28.channel('rpm')!.verified).toBe(false);
    expect(p28.channel('rpm')!.evidence).toBeNull();
    expect(p28.constants.k).toBe(2);
    expect(p28.ecu).toBe('obd1');
  });

  it('math verified follows its inputs', () => {
    const amap = loads(V2_MAP);
    expect(amap.isVerified('rpm')).toBe(true);
    expect(amap.isVerified('rpm_k')).toBe(true);
    expect(amap.isVerified('ect')).toBe(false);
    expect(amap.isVerified('nope')).toBe(false);
    expect(amap.verifiedMap()).toEqual({ rpm: true, ect: false, rpm_k: true });
  });

  it.each([
    ['schema = 3\n', 'schema 3'],
    ['schema = 2\nbogus = 1\n', 'unknown section'],
  ])('v2 rejects %j', (patch, why) => {
    expect(() => loads(patch + V1_MAP)).toThrow(why);
  });

  it.each([
    ['rate = "fast"', 'rate = "warp"', 'rate must be'],
    ['endian = "little"', 'endian = "middle"', 'endian must be'],
    ['[channel.variants.P28]\nformula', '[channel.variants.P28]\nkind = "flag"\nformula', 'cannot set'],
    ['[[math]]\nname = "rpm_k"\nexpr = "[rpm] * k"', '[[math]]\nname = "m"\nexpr = "[m]"', 'loop'],
    ['summary = "agreed with tach"', 'colour = "red"', 'unknown evidence field'],
    ['ecu_variant = "P2P"', 'ecu_variant = "P30"', 'not listed'],
    ['[constants]\nk = 1', '[constants]\nrpm = 1', 'same name'],
  ])('v2 validation: %s -> %s', (old, now, why) => {
    const text = V2_MAP.replace(old, now);
    expect(text).not.toBe(V2_MAP);
    expect(() => loads(text)).toThrow(why);
  });

  it('a formula cannot use history', () => {
    expect(() => ch('x', 0, { formula: 'avg(x, 100)' })).toThrow(/history/);
  });

  it('the version hash tracks decoding only', () => {
    const a = loads(V2_MAP);
    expect(a.versionHash).toHaveLength(12);
    expect(a.versionTag.startsWith('test-1 #')).toBe(true);
    const relabelled = loads(V2_MAP.replace('source = "test"', 'source = "test"\nlabel = "Engine"'));
    const unverified = loads(V2_MAP.replace('status = "verified"', 'status = "unverified"'));
    expect(relabelled.versionHash).toBe(a.versionHash);
    expect(unverified.versionHash).toBe(a.versionHash);
    const moved = loads(V2_MAP.replace('addr = 0x10', 'addr = 0x11'));
    const reformulated = loads(V2_MAP.replace('formula = "x - 40"', 'formula = "x - 39"'));
    expect(new Set([a.versionHash, moved.versionHash, reformulated.versionHash]).size).toBe(3);
    expect(loads(V2_MAP, 'P28').versionHash).not.toBe(a.versionHash);
  });

  it('the shipped default is v2 with three variants', () => {
    const amap = loadDefault();
    expect(amap.schema).toBe(2);
    expect(amap.variant).toBe('P2P');
    expect(new Set(Object.keys(amap.variants))).toEqual(new Set(['P2P', 'P2T', 'P28']));
    expect(amap.channels.every((c) => c.source)).toBe(true);
    for (const n of ['inj_duty', 'fuel_flow', 'total_trim', 'rpm_rate']) expect(amap.mathNames).toContain(n);
    expect(amap.alarms.map((a) => a.name)).toContain('ect_high');
    for (const variant of Object.keys(amap.variants)) expect(loadDefault(variant).names.length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------- messages and edge cases

const one = (body: string) => `[[channel]]\nname='a'\naddr=1\n${body}`;

describe('validation messages match Python', () => {
  it.each([
    ["[[channel]]\nname='a'\naddr=300\n", 'a: addr out of range'],
    ["[[channel]]\nname='a'\naddr=1.0\n", 'a: addr out of range'],
    ["[[channel]]\nname='a'\naddr=0xFE\nlength=4\n", 'a: length must be 1-4 and stay below 0x100'],
    ["[[channel]]\nname='9a'\naddr=1\n", "bad channel name '9a'"],
    ["[[channel]]\nname='a'\naddr=1\nbogus=2\n", "address map: Channel.__init__() got an unexpected keyword argument 'bogus'"],
    ["[[channel]]\nname='a'\n", "address map: Channel.__init__() missing 1 required positional argument: 'addr'"],
    ["[[channel]]\nlabel='a'\n", "address map: Channel.__init__() missing 2 required positional arguments: 'name' and 'addr'"],
    [one("kind='lamp'\n"), "a: kind must be 'value' or 'flag'"],
    [one("status='maybe'\n"), 'a: status must be one of unverified, verified, suspect'],
    [one('rate=5\n'), 'a: rate must be one of fast, medium, slow'],
    [one('formula=5\n'), 'a: formula must be text'],
    [one("formula='x +'\n"), "bad formula 'x +': expected a value, got the end"],
    [one("formula='y'\n"), "bad formula 'y': unknown name 'y'"],
    [one("formula='xx'\n"), "bad formula 'xx': unknown name 'xx' (did you mean x?)"],
    [one("formula='avg(x, 100)'\n"), "bad formula 'avg(x, 100)': a formula decodes one reading, it cannot use history (avg); make a [[math]] channel for that"],
    [one("evidence='yes'\n"), 'a: evidence must be a table'],
    ['schema = 2.5\n' + one(''), 'address map: schema must be a whole number'],
    ['schema = 0\n' + one(''), 'address map: schema must be a whole number'],
    ['schema = 3\n' + one(''), 'address map: written for map schema 3, this hondalog reads up to 2. Update hondalog or use an older map.'],
    ['schema = 2\nzzz = 1\n' + one(''), "address map: unknown section or key 'zzz' (known: alarm, base_variant, channel, constants, ecu, ecu_id, ecu_variant, math, obd2, protocol, schema, variants, version)"],
    ["[[channel]]\nname='b'\naddr=1\n[[channel]]\nname='b'\naddr=2\n", "address map: duplicate channel names ['b']"],
    ["ecu_variant = 'P30'\n" + one(''), "address map: no ECU variant 'P30' in this map"],
    ['[constants]\nk = "x"\n' + one(''), 'address map: [constants]: k must be a number'],
    ['[constants]\nk = true\n' + one(''), 'address map: [constants]: k must be a number'],
    ['constants = 5\n' + one(''), 'address map: [constants] must be a table of name = number'],
    ['[[obd2]]\nname="a"\npid=4\n' + one(''), "address map: obd2 channel 'a' has the same name as a channel"],
    ['[[obd2]]\nname="z"\npid=0\n' + one(''), 'z: pid must be a Mode 01 PID, 0x01-0xFF'],
    ['[[obd2]]\nname="z"\npid=4\nwarp=1\n' + one(''), "address map: [[obd2]] Obd2Channel.__init__() got an unexpected keyword argument 'warp'"],
    ['[[math]]\nname="m"\nexpr=""\n' + one(''), 'address map: m: no expression'],
    ['[[math]]\nname="m"\nexpr="[q]"\n' + one(''), "address map: math channel 'm': unknown name 'q' at column 1 of '[q]'"],
    ['[[math]]\nname="a"\nexpr="1"\n' + one(''), "address map: math channel 'a' has the same name as a measured channel"],
    ['[[math]]\nname="m"\nexpr="[n]"\n[[math]]\nname="n"\nexpr="[m] + 1"\n' + one(''), 'address map: math channels refer to each other in a loop: m -> n -> m'],
    ['[[alarm]]\nname="x"\n' + one(''), "address map: alarm 'x': needs a limit (low, high, rate_low, rate_high) or 'when'"],
    ['[[alarm]]\nname="x"\nhigh=5\n' + one(''), "address map: alarm 'x': low/high/rate limits need a 'channel'"],
    ['[[alarm]]\nname="x"\nchannel="q"\nhigh=5\n' + one(''), "address map: alarm 'x': unknown channel 'q'"],
    ['[[alarm]]\nname="x"\nwhen="[q] > 1"\n' + one(''), "address map: alarm 'x', when: unknown name 'q' at column 1 of '[q] > 1'"],
    ['[[alarm]]\nname="x"\nwhen="[a] > 1"\nseverity="bad"\n' + one(''), "address map: alarm 'x': severity must be 'warn' or 'critical'"],
    ['[[alarm]]\nname="x"\nwhen="[a] > 1"\nactions="beep"\n' + one(''), "address map: alarm 'x': actions must be a list"],
    ['[[alarm]]\nname="x"\nwhen="[a] > 1"\nactions=["boom"]\n' + one(''), "address map: alarm 'x': unknown action 'boom' (use tile, banner, beep, marker, capture)"],
    ['[[alarm]]\nname="x"\nchannel="a"\nlow=5\nhigh=1\n' + one(''), "address map: alarm 'x': low must be below high"],
    ['[[alarm]]\nname="x"\nwhen="[a] > 1"\n[[alarm]]\nname="x"\nwhen="[a] > 2"\n' + one(''), "address map: duplicate alarm 'x'"],
  ])('%j', (text, message) => {
    let got = '';
    try {
      loads(text);
    } catch (exc) {
      expect(exc).toBeInstanceOf(MapError);
      got = (exc as Error).message;
    }
    expect(got).toBe(message);
  });

  it('variant errors', () => {
    const text = `schema = 2\n[variants.A]\n[variants.B]\n[[channel]]\nname='a'\naddr=1\n[channel.variants.C]\naddr=2\n`;
    expect(() => loads(text)).toThrow("address map: ECU variant 'C' is not listed under [variants] (known: A, B)");
    const gone = `schema = 2\n[variants.A]\n[variants.B]\n[[channel]]\nname='a'\naddr=1\n[channel.variants.B]\nabsent=true\n`;
    expect(() => loads(gone, 'B')).toThrow("address map: variant 'B' has no channels");
    const bad = `schema = 2\n[variants.A]\n[variants.B]\n[[channel]]\nname='a'\naddr=1\n[channel.variants.B]\naddr=300\n`;
    expect(() => loads(bad, 'B')).toThrow('a: addr out of range');
    expect(loads(bad, 'A').channel('a')!.addr).toBe(1);
  });

  it('a path names the file in messages', () => {
    expect(() => loads("version = 'x'\n", null, 'my.toml')).toThrow('my.toml: no [[channel]] entries');
  });
});

describe('map details', () => {
  it('flags force 0..1 ranges; labels default to the name', () => {
    const m = loads("[[channel]]\nname='f'\naddr=1\nkind='flag'\nmax=9\ndecimals=3\n[[channel]]\nname='v'\naddr=2\nlabel='Volts'\n");
    const f = m.channel('f')!;
    expect([f.min, f.max, f.decimals, f.label]).toEqual([0, 1, 0, 'f']);
    expect(m.channel('v')!.label).toBe('Volts');
    expect(f.format(1)).toBe('1');
    expect(f.format(null)).toBe('--');
    expect(f.format(NaN)).toBe('--');
  });

  it('ecu id, units, obd2 names and the alarm contract view', () => {
    const m = loadDefault();
    expect(m.ecuId).toEqual({ addr: 0x76, length: 10 });
    expect(m.obd2Names).toEqual(['o2_post', 'calc_load']);
    expect(m.units().ect).toBe('°C');
    expect(m.units().calc_load).toBe('%');
    const ect = m.alarms.find((a) => a.name === 'ect_high')!;
    expect([ect.level, ect.high, ect.hysteresis, ect.latching, ect.priority]).toEqual(['alarm', 105, 3, true, 100]);
    const batt = m.alarms.find((a) => a.name === 'batt_low')!;
    expect([batt.level, batt.gate, batt.priority, batt.label]).toEqual(['warn', '[rpm] > 400', 50, 'Battery low']);
    expect(m.isVerified('inj_duty')).toBe(false);      // inj is unverified
    expect(m.isVerified('total_trim')).toBe(true);     // stft + ltft
    expect(m.isVerified('load')).toBe(false);          // calc_load is OBD2, not a channel
    expect(m.mathBaseInputs('fuel_flow')).toEqual(new Set(['inj', 'rpm']));
    const map = loads("[[channel]]\nname='a'\naddr=1\n");
    expect(map.ecuId).toEqual({ addr: null, length: 10 });
    expect(map.version).toBe('unversioned');
  });

  it('channel specs carry the Live group', () => {
    const m = loadDefault();
    const spec = Object.fromEntries(m.channelSpecs().map((s) => [s.name, s.group]));
    expect(spec.rpm).toBe('engine');
    expect(spec.iacv).toBe('idle');
    expect(spec.stft).toBe('fuel');
    expect(spec.ign).toBe('ignition');
    expect(spec.batt).toBe('electrical');
    expect(spec.brake).toBe('switches');
    expect(m.mathChannel('inj_duty')!.group).toBe('fuel');
  });

  it('keeps the TOML text and is an AddressMap', () => {
    const m = loads(V1_MAP);
    expect(m).toBeInstanceOf(AddressMap);
    expect(m.text).toBe(V1_MAP);
  });

  it('little-endian 4-byte values and big numbers decode exactly', () => {
    const m = loads("[[channel]]\nname='w'\naddr=0\nlength=4\nendian='little'\nformula='x'\n[[channel]]\nname='s'\naddr=0\nlength=4\nformula='signed(x, 32)'\n");
    const v = m.decode([0x01, 0x02, 0x03, 0xff]);
    expect(v.w).toBe(0xff030201);
    expect(v.s).toBe(0x010203ff);
    expect(m.decode([0xff, 0xff, 0xff, 0xfe]).s).toBe(-2);
  });
});
