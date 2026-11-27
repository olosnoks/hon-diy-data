/**
 * OBD2 (ISO 9141-2) for an ECU that never answers Honda reads: #6's
 * Iso9141Link + Obd2Session behind #1's Poller IsoFallback. The poller tries
 * it after ~12 s of unanswered Honda reads (protocol "auto"); in OBD2-only
 * mode it steps next() and hands out every Mode 01 reply (onPid).
 *
 * The 5-baud init needs ~200 ms bit edges: a hidden or minimised tab has its
 * timers throttled and the init fails. `onInit` tells the UI so it can say
 * "keep hondalog in front while connecting".
 */
import type { Clock as LinkClock } from '../core/link/clock';
import type { IsoFallback, IsoLiveSession } from '../core/link/poller';
import { REAL as OBD_REAL, type Clock as ObdClock } from '../core/obd2/clock';
import { Iso9141Link, initText } from '../core/obd2/iso9141';
import { Obd2Session, PID_CHANNEL } from '../core/obd2/obd2';
import type { Transport } from '../core/types';

/** The link's Clock as the OBD2 clock (precise sleepUntil on the real clock). */
export function obdClock(c: LinkClock): ObdClock {
  if (!c.virtual) return OBD_REAL;
  return {
    virtual: true,
    now: () => c.now(),
    sleep: (s) => c.sleep(s),
    sleepUntil: (t) => c.sleep(t - c.now()),
  };
}

/** PIDs worth polling live: the ones with a map channel, readiness and the bitmaps left out. */
export function livePids(supported: number[]): number[] {
  const wanted = supported.filter((p) => PID_CHANNEL[p] !== undefined && p !== 0x01);
  return wanted.length ? wanted : [0x0c, 0x0d, 0x05];
}

export interface Obd2FallbackHooks {
  /** 'start' before the init, 'ok' / 'failed' after; hidden = the page was hidden during the init. */
  onInit?: (phase: 'start' | 'ok' | 'failed', detail: string, hidden: boolean) => void;
  isHidden?: () => boolean;
}

export function obd2Fallback(hooks: Obd2FallbackHooks = {}): IsoFallback {
  const isHidden = hooks.isHidden ?? (() => typeof document !== 'undefined' && document.hidden);
  return {
    async open(transport: Transport, clock: LinkClock, hondaBaud: number): Promise<IsoLiveSession> {
      let hidden = isHidden();
      const watch = () => { if (isHidden()) hidden = true; };
      const doc = typeof document !== 'undefined' ? document : null;
      doc?.addEventListener('visibilitychange', watch);
      hooks.onInit?.('start', 'OBD2 5-baud init', hidden);
      const link = new Iso9141Link(transport, { clock: obdClock(clock), hondaBaud });
      let session: Obd2Session;
      let pids: number[];
      try {
        const init = await link.connect();
        session = new Obd2Session(link);
        pids = livePids(await session.supportedPids());
        hooks.onInit?.('ok', initText(init), hidden);
      } catch (e) {
        watch();
        hooks.onInit?.('failed', (e as Error).message, hidden);
        await link.close().catch(() => undefined);
        throw e;
      } finally {
        doc?.removeEventListener('visibilitychange', watch);
      }
      let i = 0;
      return {
        label: link.init ? initText(link.init) : 'ISO 9141-2',
        async next() {
          const pid = pids[i % pids.length]!;
          i++;
          const raw = await session.readPidRaw(pid);
          return { pid, raw, emit: i % pids.length === 0 };
        },
        async close() {
          await link.close();
        },
      };
    },
  };
}
