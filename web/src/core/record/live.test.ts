// @vitest-environment node
/** The live recording path: poller callbacks, math and alarms, the black box and the handheld (live.py LiveSession). */
import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { HondalogDb } from '../store/db';
import { readDrive } from '../store/reader';
import { type AlarmEventLike, LiveSession, type Pipeline, blackboxKeyOn } from './live';
import { recorderMapFromToml } from './testing';

const amap = recorderMapFromToml(readFileSync(join(resolve(__dirname), '../store/fixtures/addressmap.toml'), 'utf8'));

/** A math channel (duty = rpm / 100) and one alarm (rpm > 5000, capture). */
function pipeline(): Pipeline {
  let raised = false;
  return {
    mathNames: ['duty'],
    mathChannels: [{ name: 'duty', units: '%', decimals: 1, min: 0, max: 100 }],
    process(values, t, link) {
      const rpm = values.rpm ?? null;
      const out = { ...values, duty: link && rpm !== null ? rpm / 100 : null };
      const events: AlarmEventLike[] = [];
      if (rpm !== null && rpm > 5000 && !raised) {
        raised = true;
        events.push({ t, name: 'over_rev', kind: 'raised', severity: 'alarm', value: rpm, channel: 'rpm', unverified: false,
          markerText: 'ALARM over_rev', message: 'rpm above 5000', actions: ['marker', 'capture'] });
      }
      return { values: out, events };
    },
  };
}

describe('LiveSession', () => {
  it('records reads, math, alarms, link loss, GPS and the handheld', async () => {
    const hdb = await HondalogDb.open(null);
    let mono = 0;
    const live = new LiveSession(hdb, amap, {
      pipeline: pipeline(), recorder: { background: false, checkpointS: 0, epochUnix: 1795765862 },
      logMeta: () => ({ read_plan: ['00+3'], link: { requests: 10, ok: 10 } }), monotonic: () => mono,
    });
    const fired: string[] = [];
    live.onTriggerOut = (f) => fired.push(f.rule.name);
    live.onMarkOut = () => true;                                 // a two-way handheld got it
    expect(live.mark()).toBe(0);                                 // not recording
    const rec = live.startRecording();
    for (let i = 0; i < 100; i++) {
      const t = i * 0.05;
      const rpm = i === 60 ? 5200 : 800;
      live.onBlock({ t: t + 0.004, addr: 0, data: new Uint8Array([(rpm * 4) >> 8, (rpm * 4) & 0xff, 0]), latencyS: 0.004 });
      live.onSample({ t, link: true, values: { rpm } });
      if (i % 10 === 0) live.feedGps(t, { gps_lat: 52.1, gps_lon: 4.3, gps_speed: 0, gps_heading: 0, gps_sats: 7, gps_fix: 1, gps_hdop: 1, gps_utc: 0 }, 'handheld');
    }
    expect(live.mark()).toBe(2);                                 // the alarm's marker was number 1
    mono = 1;
    live.onCydEvent({ t: 5.0, code: 1, name: 'marker', src: 0, aux: 0, value: 1, tDevUs: 0, text: 'marker', isStall: false, isMarker: true, isDtc: false });  // the echo
    live.onCydEvent({ t: 5.1, code: 1, name: 'marker', src: 0, aux: 0, value: 2, tDevUs: 0, text: 'marker', isStall: false, isMarker: true, isDtc: false });  // a real press
    live.onCydEvent({ t: 5.2, code: 9, name: 'dtc', src: 0, aux: 0, value: 0, tDevUs: 0, text: 'P0135', isStall: false, isMarker: false, isDtc: true });
    live.onLinkError({ t: 5.25, addr: 0x12, length: 4, kind: 'NoReply', attempt: 1, message: 'no reply' });
    live.onSample({ t: 5.3, link: false, values: {} });
    mono = 5;                                                    // long after the laptop asked the box
    live.onBlackboxEvent({ t: 5.4, text: 'IG1 off', code: 36, name: 'ig1_off', eventNo: 1, tDevUs: 0, startedFile: true });
    expect(live.frames.length).toBe(101);
    const stopped = await live.stopRecording();
    expect(stopped).toBe(rec);
    expect(live.recording).toBe(false);
    const log = readDrive(hdb.conn, rec.ref.drive);
    expect([...log.streams.keys()].sort()).toEqual(['ecu/00+3', 'ecu/link', 'gps', 'math']);
    expect(log.series('rpm').v[0]).toBe(800);
    expect(log.series('rpm').t[0]).toBe(0);                      // completion minus its age
    expect(log.series('duty').v[0]).toBe(8);
    expect(log.streams.get('gps')!.info.source).toBe('handheld');
    expect(log.meta.read_plan).toEqual(['00+3']);
    const texts = log.events.map((e) => `${e.kind}:${e.text}`);
    expect(texts).toContain('alarm:ALARM over_rev');
    expect(texts).toContain('marker:ALARM over_rev');
    expect(texts).toContain('marker:MARK 2');
    expect(texts).toContain('marker:MARK (handheld button 2)');
    expect(texts.filter((x) => x.startsWith('marker:MARK (handheld'))).toHaveLength(1);
    expect(texts).toContain('dtc:P0135');
    expect(texts).toContain('blackbox:IG1 off');
    expect(texts).toContain('link_error:NoReply 12+4 #1');
    expect(hdb.conn.all('SELECT kind, rule FROM captures ORDER BY t_trigger')).toEqual([['alarm', 'alarm_over_rev']]);
    expect(fired).toEqual(['alarm_over_rev']);                   // the box is asked to save its side
    expect(hdb.conn.get('SELECT state, name FROM alarms')).toEqual(['raised', 'over_rev']);
    // the box saw IG1 go off inside the open window: one bundle with both firings
    expect(rec.events.map((e) => e.rule)).toEqual(['alarm_over_rev']);
    expect(JSON.parse(String(hdb.conn.get('SELECT fired FROM captures')![0])).map((f: { rule: string }) => f.rule)).toEqual(['alarm_over_rev', 'external']);
  });

  it('a black box frame keeps the drive open with the key on', async () => {
    expect(blackboxKeyOn({ bb_ig1: 12.4 })).toBe(true);
    expect(blackboxKeyOn({ bb_ig1: 0.2, bb_ecub: 0.1 })).toBe(false);
    expect(blackboxKeyOn({ bb_running: 1 })).toBe(true);
    expect(blackboxKeyOn({ bb_ig1: NaN })).toBe(false);
    const hdb = await HondalogDb.open(null);
    const live = new LiveSession(hdb, amap, { recorder: { background: false, driveGapS: 5 } });
    const rec = live.startRecording();
    live.onSample({ t: 0, link: true, values: { rpm: 800 } });
    for (let i = 1; i < 200; i++) {
      live.onSample({ t: i * 0.1, link: false, values: {} });
      live.onBlackboxSample({ t: i * 0.1, link: true, values: { bb_ig1: 12.4, bb_ecub: 0.2 } });
    }
    expect(rec.parked).toBe(false);                              // 20 s of ECU silence, key on: one drive
    for (let i = 200; i < 300; i++) live.onBlackboxSample({ t: i * 0.1, link: true, values: { bb_ig1: 0, bb_ecub: 0 } });
    expect(rec.parked).toBe(true);
    await live.stopRecording();
    expect(readDrive(hdb.conn, rec.ref.drive).meta.end_reason).toBe('key_off');
  });
});
