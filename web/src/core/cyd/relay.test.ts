import { describe, expect, it } from 'vitest';
import { hex, utf8 } from './bytes';
import { HandheldCompanion } from './companion';
import { FakeCyd, until } from './fake_cyd';
import { MAP_DIFFERS, Relay, type RelayPoller, TOO_OLD, TX_ONLY, linkFields } from './relay';
import * as P from './serial_proto';

const HASH = '44979f9238b1';
const RELAY_CAPS = P.CAP_RX | P.CAP_FILES | P.CAP_PUT | P.CAP_LOG | P.CAP_MAPUP | P.CAP_HL | P.CAP_RELAY;

function poller(fake: FakeCyd, fields: Partial<RelayPoller> = {}): RelayPoller {
  return { state: 'ok', mode: 'honda', t0: 0, hz: 19.37, sweeps: 77, ecuId: utf8('37820-P2P'), now: () => fake.clock.now(), ...fields };
}

async function session(opts: ConstructorParameters<typeof FakeCyd>[0] = {}, force = false, mapHash = HASH) {
  const fake = new FakeCyd({ caps: RELAY_CAPS, ...opts });
  const hh = new HandheldCompanion(fake, { clock: fake.clock, mapHash, exclude: 'COM21' }).start();
  await until(() => hh.hello !== null, fake.clock);
  const pl = poller(fake);
  const relay = new Relay(hh, pl, mapHash, force, 1024, fake.clock);
  return { fake, hh, pl, relay };
}

describe('relay (test_hh_relay)', () => {
  it('link fields', () => {
    expect(linkFields({ state: 'ok', mode: 'honda' })).toEqual([P.LINK_OK, true, false]);
    expect(linkFields({ state: 'ok', mode: 'obd2' })).toEqual([P.LINK_ISO_ONLY, true, true]);
    expect(linkFields({ state: 'iso_only' })).toEqual([P.LINK_ISO_ONLY, true, true]);
    expect(linkFields({ state: 'connecting', mode: 'honda' })).toEqual([P.LINK_CONNECTING, false, false]);
    expect(linkFields({ state: 'no_reply' })).toEqual([P.LINK_OFF, false, false]);
    expect(linkFields({ state: 'port_lost' })).toEqual([P.LINK_OFF, false, false]);
  });

  it('Honda reads are relayed byte for byte', async () => {
    const { fake, hh, relay } = await session();
    expect(relay.status).toBe('');
    expect(relay.active).toBe(true);
    const data = Uint8Array.of(0x12, 0x34, 0x56, 0x78);
    relay.onBlock({ mono: 10.5, latencyS: 0.012, addr: 0x10, data });
    relay.onSample({ link: true, t: 10.5, mono: 10.5, latency: 0.012 });
    await relay.flush();
    const reads = fake.sent(P.SR_RELAY_READ);
    expect(reads.map(hex)).toEqual([hex(P.packRelayRead(Math.trunc((10.5 - 0.012) * 1e6), 0x10, data, 12000))]);
    const ticks = fake.sent(P.SR_RELAY_TICK);
    expect(ticks.length).toBe(1);
    expect(P.RELAY_TICK.unpack(ticks[0]!)).toEqual([10_500_000, 12000, 1937, 77]);
    const links = fake.sent(P.SR_RELAY_LINK);
    expect(links.length).toBe(1);
    const [, state, up, iso, n] = P.RELAY_LINK.unpack(links[0]!);
    expect([state, up, iso, n]).toEqual([P.LINK_OK, 1, 0, 9]);
    expect(new TextDecoder().decode(links[0]!.subarray(12))).toBe('37820-P2P');
    expect(relay.text()).toBe('relay to handheld: 3 sent');
    await hh.stop();
  });

  it('OBD2-only replies go as mode 0x41', async () => {
    const { fake, hh, relay } = await session();
    relay.onPid(2.0, 0x0c, Uint8Array.of(0x1a, 0xf8));
    await relay.flush();
    expect(fake.sent(P.SR_RELAY_ISO).map(hex)).toEqual([hex(P.packRelayIso(2_000_000, 0x0c, Uint8Array.of(0x1a, 0xf8)))]);
    await hh.stop();
  });

  it('the link goes on a change and once a second', async () => {
    const { fake, hh, pl, relay } = await session();
    relay.checkLink();
    relay.checkLink();
    expect(relay.queued).toBe(1);
    (pl as { state: string }).state = 'connecting';
    relay.checkLink();
    expect(relay.queued).toBe(2);
    fake.clock.advance(1.1);
    relay.checkLink();
    expect(relay.queued).toBe(3);
    await hh.stop();
  });

  it('firmware 0.1.0 gets nothing and says why; force relays to it', async () => {
    const { fake, hh, relay } = await session({ caps: P.CAP_RX | P.CAP_HL });
    expect(relay.status).toBe(TOO_OLD);
    expect(relay.text()).toBe(TOO_OLD);
    relay.onBlock({ mono: 1, latencyS: 0.01, addr: 0, data: Uint8Array.of(1) });
    await relay.flush();
    expect(fake.sent(P.SR_RELAY_READ).length).toBe(0);
    const forced = new Relay(hh, poller(fake), HASH, true, 1024, fake.clock);
    forced.onBlock({ mono: 1, latencyS: 0.01, addr: 0, data: Uint8Array.of(1) });
    await forced.flush();
    expect(fake.sent(P.SR_RELAY_READ).length).toBe(1);
    await hh.stop();
  });

  it('a map that differs is said; once pushed it relays', async () => {
    const { fake, hh, relay } = await session({ mapHash: '000000000000' });
    expect(relay.status).toBe(MAP_DIFFERS);
    expect(hh.mapMismatch).toBe(true);
    expect(hh.warning).toMatch(/#000000000000/);
    fake.mapHash = HASH;
    fake.bootNo += 1; // a push takes effect at the next power-up
    await until(() => hh.hello?.mapHash === HASH, fake.clock);
    expect(relay.status).toBe('');
    await hh.stop();
  });

  it('a tx-only handheld is never written to', async () => {
    const fake = new FakeCyd({ txOnly: true });
    const hh = new HandheldCompanion(fake, { clock: fake.clock, mapHash: HASH }).start();
    await until(() => hh.hello !== null, fake.clock);
    const relay = new Relay(hh, poller(fake), HASH, true, 1024, fake.clock);
    expect(relay.status).toBe(TX_ONLY);
    relay.onBlock({ mono: 1, latencyS: 0.01, addr: 0, data: Uint8Array.of(1) });
    await relay.flush();
    await until(() => fake.clock.now() > 5, fake.clock);
    expect(fake.rxIgnored).toBe(0);
    await expect(hh.mark()).rejects.toThrow(/tx-only/);
    await hh.stop();
  });

  it('a stalled USB link never holds up the poller: the oldest frames are dropped', async () => {
    const { fake, hh } = await session();
    const relay = new Relay(hh, poller(fake), HASH, false, 10, fake.clock);
    for (let i = 0; i < 25; i++) relay.onBlock({ mono: i, latencyS: 0.01, addr: i, data: Uint8Array.of(i) });
    expect(relay.queued).toBe(25);
    expect(relay.dropped).toBe(15);
    fake.failWrites = 3; // and a write that fails is counted, the rest go
    await relay.flush();
    expect(relay.failed).toBe(3);
    expect(relay.sent).toBe(7);
    const addrs = fake.sent(P.SR_RELAY_READ).map((p) => p[8]);
    expect(addrs).toEqual([18, 19, 20, 21, 22, 23, 24]);
    expect(relay.text()).toBe('relay to handheld: 7 sent, 15 dropped');
    await hh.stop();
  });
});
