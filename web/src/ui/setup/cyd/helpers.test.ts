import { describe, expect, it } from 'vitest';
import {
  cfgParse, cfgProblems, cfgSetValue, cfgValues, DEFAULT_CFG, fmtBytes, fmtDur, KNOWN_KEYS, progressText, statusRows, uploadTarget,
} from './helpers';

describe('CYD tab helpers (tests/test_handheld_screen.py test_pure_helpers)', () => {
  it('upload targets, progress, status rows, sizes', () => {
    expect(uploadTarget('/x/handheld.cfg')).toEqual(['/HANDHELD.CFG', '']);
    expect(uploadTarget('C:\\x\\MAP.DAT')).toEqual(['/MAP/MAP.DAT', '']);
    expect(uploadTarget('/x/a very long name.dat')[0]).toBeNull();
    const [pct, text] = progressText(5 * 1048576, 10 * 1048576, 10);
    expect(pct).toBe(50);
    expect(text).toContain('~0:10 left');
    const rows = statusRows({});
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.every((r) => r.length === 4)).toBe(true);
    expect(rows[0]![2]).toBe('NO  ?');
    expect(fmtBytes(2048)).toBe('2.0 KB');
    expect(fmtDur(3725)).toBe('1:02:05');
    const ok = statusRows({ link: { state: 2, name: 'K-line', hz: 11.4 }, gps: { fix: 1, sats: 9, lat: 1, lon: 2 }, time: { source: 'gps', unix_ms: 0 } });
    expect(ok[0]).toEqual(['ECU link', 'link', 'OK  K-line', true]);
    expect(ok[4]![2]).toBe('OK  fix, 9 satellites');
  });
});

describe('HANDHELD.CFG text (tests/test_cfgtext.py, golden against Python)', () => {
  it('parses settings and commented ones', () => {
    const entries = cfgParse(DEFAULT_CFG);
    expect(entries.map((e) => e.key)).toContain('rotate');
    expect(entries.find((e) => e.key === 'rotate')!.enabled).toBe(false);
    expect(entries.find((e) => e.key === 'units')!.value).toBe('us');
  });

  it('sets a value and keeps its comment', () => {
    const a = cfgSetValue(DEFAULT_CFG, 'rotate', '180');
    expect(a.split('\n')[19]).toBe("rotate = 180   # display: 0 or 180 (upside down); unset = the firmware's built-in default");
    expect(cfgValues(a).rotate).toBe('180');
    const b = cfgSetValue(a, 'rotate', '0', false);
    expect(b.split('\n')[19]).toBe("# rotate = 0   # display: 0 or 180 (upside down); unset = the firmware's built-in default");
    expect(cfgSetValue('x = 1\n', 'units', 'metric')).toBe('x = 1\nunits = metric\n');
  });

  it('flags lines the handheld will skip', () => {
    expect(cfgProblems(DEFAULT_CFG, KNOWN_KEYS)).toEqual([]);
    expect(cfgProblems('# hi\nfoo\nnonsense_key = 1\nunits = us\n', KNOWN_KEYS)).toEqual(["line 2: not 'key = value'", "line 3: unknown setting 'nonsense_key'"]);
  });
});
