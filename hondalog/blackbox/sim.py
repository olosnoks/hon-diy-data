"""Black box simulator: a fake car, the firmware's logic in Python, and a
fake USB port in front of it.

Three layers, each usable on its own:

``Car``
    Generates what the box would see on its eight inputs: four rails with
    alternator ripple, cranking dip and relay bounce; tach, ignition trigger
    and injector edges with microsecond times; the button. ``fail()`` injects
    a stall: ``"main_relay"``, ``"igniter"``, ``"fuel_pump"``, ``"key_off"``
    or ``"battery"``.

``CoreModel``
    What the firmware does with that: 1 kHz records with min/max, RPM and
    injector timing from the edges, the trigger rules, the 30 s ring and the
    event file. Mirrors ``bb_engine.c``.

``SimDevice``
    ``CoreModel`` plus the USB protocol, so ``BlackboxReader`` cannot tell it
    from the real box. ``SimPort`` serves it in-process (port name
    ``bbsim:`` or ``bbsim:main_relay@20``); ``serve`` puts it on a pty
    (``hondalog-bbsim``).
"""

from __future__ import annotations

import argparse
import math
import os
import random
import select
import struct
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import format as F
from .eventfile import BBEventRecord, BlackboxEvent, write_event
from .triggers import (ACT_CAPTURE, AGE_MAX_MS, Config, ConfigError, EdgeTracker, Fired,
                       TriggerEngine, TrigInput)

BBSIM_SYMLINK = "/tmp/hondalog-bbsim"
BBSIM_PREFIX = "bbsim:"
FAULTS = ("main_relay", "igniter", "fuel_pump", "key_off", "battery")
SUB = 5                         # ADC readings per rail per millisecond (5 kHz)


@dataclass
class Tick:
    """One millisecond of inputs. ``sub[k][ch]`` is rail ``ch`` (mV) at
    sub-sample ``k``; edges are (t_us, channel, level) with level 1 = active."""
    n: int
    sub: list
    edges: list
    button: int = 0


class Car:
    """A D16 that starts, idles, revs now and then, and dies on request."""

    def __init__(self, fault: str | None = None, fault_at: float = 20.0, seed: int = 1,
                 key_on_at: float = 0.3, crank_at: float = 1.2, start_at: float = 2.0,
                 rev_every: float = 7.0, restart_after: float | None = None, sub: int = SUB):
        if fault not in (None, "none", *FAULTS):
            raise ValueError(f"unknown fault {fault!r}; choose from {FAULTS}")
        if sub not in (1, SUB):
            raise ValueError(f"sub must be 1 or {SUB}")
        #: ADC readings generated per millisecond. ``SUB`` is what the box
        #: samples (min/max inside each record, 200 us glitches). 1 is the
        #: cheap model for a simulator that only feeds a screen: one reading
        #: per millisecond, so a record's min and max equal its mean. The
        #: rails, the pulses and every fault behave the same at 1 kHz.
        self.sub = sub
        self.rng = random.Random(seed)
        self.fault_plan = None if fault in (None, "none") else (fault, fault_at)
        self.key_on_at, self.crank_at, self.start_at = key_on_at, crank_at, start_at
        self.rev_every, self.restart_after = rev_every, restart_after
        self.key = False
        self.relay_ok = self.igniter_ok = self.pump_ok = True
        self.fault: str | None = None
        self.fault_t: float | None = None
        self.batt_dip_until = 0.0
        self.rpm = 0.0
        self.angle = 0.0                    # crank revolutions
        self.key_t = None                   # when IG1 last came on
        self.key_off_t = None
        self.last_turn_t = -10.0
        self._glitches: list = []           # (t0_s, t1_s) ECU +B forced to 0
        self._lvl = [12450.0, 0.0, 0.0, 0.0, 12450.0]       # vbat ecub pump ig1 alt
        self._vb = 12450.0
        self._pend: list = []               # falling edges to come: (t_us, ch)
        self._act = [0, 0, 0]
        self._button_until = -1
        self._next_rev = start_at + rev_every
        self._rev_t0 = None

    # -- controls --

    def fail(self, kind: str, t: float) -> None:
        """Inject a fault at time ``t`` (seconds)."""
        if kind not in FAULTS:
            raise ValueError(f"unknown fault {kind!r}")
        self.fault, self.fault_t = kind, t
        if kind == "main_relay":
            self.relay_ok = False
        elif kind == "igniter":
            self.igniter_ok = False
        elif kind == "fuel_pump":
            self.pump_ok = False
        elif kind == "key_off":
            self.key = False
            self.key_off_t = t
        elif kind == "battery":
            self.batt_dip_until = t + 0.4

    def restart(self, t: float) -> None:
        self.relay_ok = self.igniter_ok = self.pump_ok = True
        self.fault = self.fault_t = None
        self.key_on_at, self.crank_at, self.start_at = t, t + 0.9, t + 1.7
        self.key = False
        self._next_rev = self.start_at + self.rev_every

    def press_button(self, t: float, hold_s: float = 0.15) -> None:
        self._button_until = int((t + hold_s) * 1000)

    # -- one millisecond --

    def tick(self, n: int) -> Tick:
        t = n / 1000.0
        if self.fault_plan and t >= self.fault_plan[1]:
            kind = self.fault_plan[0]
            self.fault_plan = None
            self.fail(kind, t)
        if self.fault_plan and self.fault_plan[0] == "main_relay" and not self._glitches:
            # a cracked solder joint chatters before it lets go
            t0 = self.fault_plan[1]
            self._glitches = [(t0 - 0.400, t0 - 0.3988), (t0 - 0.150, t0 - 0.148)]
        if (self.fault and self.restart_after is not None
                and t >= self.fault_t + self.restart_after):
            self.restart(t)
        if not self.key and self.fault != "key_off" and t >= self.key_on_at and (
                self.key_t is None or self.key_on_at > self.key_t):
            self.key, self.key_t = True, t

        # the main relay pulls in 12 ms after IG1 and hangs on 5 ms after it goes
        relay = self.relay_ok and self.key_t is not None and (
            (self.key and t - self.key_t >= 0.012)
            or (not self.key and self.key_off_t is not None and t - self.key_off_t < 0.005))
        ecu = relay
        turning = self.rpm > 60
        if turning:
            self.last_turn_t = t
        fuel_starved = (not self.pump_ok) and t - self.fault_t > 1.1
        firing = ecu and self.igniter_ok and not fuel_starved and self.rpm > 150
        cranking = self.key and self.crank_at <= t < self.start_at
        started = self.key and t >= self.start_at

        # engine speed
        if cranking and not (started and firing):
            target, tau = 230.0 + 25.0 * math.sin(t * 38.0), 0.08
        elif started and firing:
            target, tau = 790.0 + 12.0 * math.sin(t * 5.1), 0.25
            if self.rev_every and t >= self._next_rev:
                self._next_rev, self._rev_t0 = t + self.rev_every, t
            if self._rev_t0 is not None:
                ph = (t - self._rev_t0) / 2.0
                if ph >= 1.0:
                    self._rev_t0 = None
                else:
                    target += 2300.0 * math.sin(math.pi * ph) ** 2
                    tau = 0.12
            if not self.pump_ok and t - self.fault_t > 0.6:     # leaning out: ragged
                target -= 250.0 * self.rng.random()
                tau = 0.05
        else:
            target, tau = 0.0, 0.0
        if tau:
            self.rpm += (target - self.rpm) * (0.001 / tau)
        else:
            self.rpm = max(0.0, self.rpm - 0.75)                # coast down, ~750 rpm/s
        rpm = self.rpm

        # battery
        if started and firing and rpm > 500:
            vb = 14200.0 + 45.0 * math.sin(self.angle * 2 * math.pi * 6)
        elif cranking:
            vb = 10300.0 + 350.0 * math.sin(self.angle * 2 * math.pi * 2)
        else:
            vb = 12450.0
        if t < self.batt_dip_until:
            vb = 7600.0
        self._vb += (vb - self._vb) * (0.25 if t < self.batt_dip_until + 0.05 else 0.02)
        vbat = self._vb

        pump_on = (ecu and self.pump_ok and self.key_t is not None
                   and (t - self.key_t < 2.0 or t - self.last_turn_t < 1.5))
        want = (vbat, vbat - 180.0 if relay else 0.0, vbat - 350.0 if pump_on else 0.0,
                vbat - 120.0 if self.key else 0.0, vbat + 150.0 if rpm > 500 else vbat)
        alpha = (1.0, 0.63, 0.18 if not pump_on else 0.63, 0.5, 1.0)   # per 200 us step
        sub = []
        if self.sub == 1:
            # one reading at the end of the millisecond: the five 200 us filter
            # steps in closed form, and uniform noise of the same spread (12 mV rms)
            lvls, rnd, row = self._lvl, self.rng.random, []
            glitch = bool(self._glitches) and any(a <= t < b for a, b in self._glitches)
            for ch in range(5):
                w = 0.0 if glitch and ch == F.A_ECUB else want[ch]
                a = alpha[ch]
                lvl = w if a >= 1.0 else w + (lvls[ch] - w) * (1.0 - a) ** SUB
                lvls[ch] = lvl
                v = lvl + (rnd() - 0.5) * 41.57
                row.append(0 if v < 0 else int(v))
            sub.append(row)
        g = self.rng.gauss
        for k in range(SUB if self.sub != 1 else 0):
            ts = t - 0.001 + (k + 1) * 0.0002
            row = []
            for ch in range(5):
                w = want[ch]
                if ch == F.A_ECUB and self._glitches and any(a <= ts < b for a, b in self._glitches):
                    w = 0.0
                lvl = self._lvl[ch] + (w - self._lvl[ch]) * alpha[ch]
                self._lvl[ch] = lvl
                v = lvl + g(0.0, 12.0)
                row.append(0 if v < 0 else int(v))
            sub.append(row)

        # pulses
        edges = []
        t0_us = (n - 1) * 1000
        end_us = n * 1000
        if ecu and rpm > 60:
            d = rpm / 60000.0
            a0, a1 = self.angle, self.angle + d
            period_us = 30e6 / rpm                              # two pulses per revolution
            if int(a1 * 2) != int(a0 * 2):                      # ignition trigger starts dwell
                frac = (math.ceil(a0 * 2) / 2 - a0) / d if d else 0.0
                te = t0_us + int(frac * 1000)
                dwell = int(min(3500.0, 0.55 * period_us))
                self._rise(edges, te, F.E_IGN, dwell)
                if self.igniter_ok:
                    self._rise(edges, te + 30, F.E_TACH, dwell)
            if int(a1 / 2 + 0.25) != int(a0 / 2 + 0.25):        # one injector: every 2 revs
                frac = ((math.ceil(a0 / 2 + 0.25) - 0.25) * 2 - a0) / d if d else 0.0
                te = t0_us + int(max(0.0, min(0.999, frac)) * 1000)
                if cranking and not firing:
                    pw = 8000.0
                else:
                    pw = 2600.0 + 0.9 * max(0.0, rpm - 800.0)
                    if not self.pump_ok:
                        pw *= 1.0 + min(0.5, (t - self.fault_t) * 0.4)   # trims chase the lean
                self._rise(edges, te, F.E_INJ, int(min(pw, 0.9 * 2 * period_us)))
            self.angle = a1
        elif not ecu:
            for te, ch in self._pend:                           # outputs fall with the ECU
                if self._act[ch]:
                    edges.append((max(t0_us, min(te, end_us - 1)), ch, 0))
                    self._act[ch] = 0
            self._pend.clear()
            self.angle += rpm / 60000.0
        else:
            self.angle += rpm / 60000.0
        keep = []
        for te, ch in self._pend:
            if te < end_us:
                if self._act[ch]:
                    edges.append((max(te, t0_us), ch, 0))
                    self._act[ch] = 0
            else:
                keep.append((te, ch))
        self._pend = keep
        edges.sort()
        return Tick(n, sub, edges, 1 if n <= self._button_until else 0)

    def _rise(self, edges, te, ch, width_us):
        if self._act[ch]:
            return
        self._act[ch] = 1
        edges.append((te, ch, 1))
        self._pend.append((te + max(200, width_us), ch))


# ---- the firmware's logic ---------------------------------------------------

class CoreModel:
    """Records, triggers, ring and event files; mirrors bb_engine.c."""

    MARGIN_S = 5

    def __init__(self, cfg: Config | None = None, out_dir: Path | None = None,
                 boot_us: int = 0, device: str = "SIM", last_event_no: int = 0, boot_no: int = 1):
        self.cfg = cfg or Config()
        self.out_dir = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="hondalog-bbsim-"))
        (self.out_dir / "EVENTS").mkdir(parents=True, exist_ok=True)
        self.boot_us, self.device, self.boot_no = boot_us, device, boot_no
        v = self.cfg.values
        self._cap = (v["pre_s"] + v["post_s"] + self.MARGIN_S) * 1000
        self._ring = np.zeros(self._cap, dtype=F.SAMPLE_DTYPE)
        self._ring_t = np.zeros(self._cap, dtype=np.int64)
        self.n_rec = 0
        self._edges: list = []              # (t_us, ch, level), trimmed to the ring span
        self.events: list[BBEventRecord] = []
        self.trig = TriggerEngine(self.cfg)
        self.trackers = [EdgeTracker() for _ in F.EDGE]
        self._last_edge = [None, None, None]
        self.event_no = last_event_no
        self.captures = 0
        self.last_reason = 0
        self.unix_us_at_t0 = 0
        self.time_valid = False
        self._capture = None                # (trigger Fired, record index)
        self._last_capture_end = None
        self.latest = None                  # (t_us, record tuple)
        self.live_min = [0xFFFF] * 5
        self.finished: list[Path] = []      # event files written, oldest first
        self.new_events: list[tuple[BBEventRecord, int, bool]] = []     # for the link

    @property
    def capturing(self) -> bool:
        return self._capture is not None

    def reload_rules(self) -> None:
        """Pick up changed settings. Like the firmware, the engine keeps what
        it knows (IG1 on, engine running); only the rule table is new."""
        old, new = self.trig, TriggerEngine(self.cfg)
        for attr in ("_ig1_on", "_ig1_cnt", "running", "ign_running", "_run_cnt", "_ign_run_cnt",
                     "_qual", "_prev_mv"):
            setattr(new, attr, getattr(old, attr))
        self.trig = new

    def event_path(self, no: int) -> Path:
        return self.out_dir / "EVENTS" / f"EV{no % 1000000:06d}.BBX"

    def _allowed(self, code: int, t_us: int) -> bool:
        if self._capture is not None:
            return False
        if code in (F.R_BUTTON, F.R_HOST, F.R_BROWNOUT) or self._last_capture_end is None:
            return True
        return t_us >= self._last_capture_end + self.cfg["holdoff_s"] * 1_000_000

    def add_event(self, t_us: int, code: int, value: int = 0, src: int = 0xFF, action: int = 0) -> None:
        rec = BBEventRecord(t_us, code, src, value, action)
        self.events.append(rec)
        starts = False
        if 1 <= code <= F.R_MAX_TRIGGER:
            self.last_reason = code
            if action == ACT_CAPTURE and self._allowed(code, t_us) and self.n_rec:
                self._capture = (rec, self.n_rec)
                starts = True
        self.new_events.append((rec, self.event_no + 1 if starts else 0, starts))
        if len(self.events) > 128:
            del self.events[:-128]

    def host_trigger(self) -> None:
        if self.latest:
            self.add_event(self.latest[0], F.R_HOST, action=ACT_CAPTURE)

    def feed(self, tick: Tick) -> None:
        v = self.cfg.values
        t_us = self.boot_us + tick.n * 1000
        min_us, timeout_us = v["edge_min_us"], v["pulse_timeout_ms"] * 1000
        for te, ch, level in tick.edges:
            te += self.boot_us
            self.trackers[ch].feed(te, level, min_us)
            self._last_edge[ch] = te
            self._edges.append((te, ch, level))
        if len(tick.sub) == 1:
            mean = lo = hi = tuple(tick.sub[0])
        else:
            cols = list(zip(*tick.sub))
            mean = tuple([sum(c) // len(c) for c in cols])
            lo = tuple([min(c) for c in cols])
            hi = tuple([max(c) for c in cols])
        tach, ign, inj = self.trackers
        rpm_tach = tach.rpm(t_us, v["tach_ppr"], timeout_us)
        rpm_ign = ign.rpm(t_us, v["ign_ppr"], timeout_us)
        pw = min(inj.pw_us, 65535)
        duty = inj.duty200(t_us, 2 * timeout_us)
        age = tuple([AGE_MAX_MS if le is None else min(AGE_MAX_MS, max(0, t_us - le) // 1000)
                     for le in self._last_edge])
        fired = self.trig.step(TrigInput(t_us, mean, lo, hi, rpm_tach, rpm_ign, pw, duty, age,
                                         tick.button))
        dig = ((F.DIG_TACH if tach.level else 0) | (F.DIG_IGN if ign.level else 0)
               | (F.DIG_INJ if inj.level else 0) | (F.DIG_BUTTON if tick.button else 0)
               | (F.DIG_IG1_ON if self.trig.ig1_on else 0)
               | (F.DIG_RUNNING if self.trig.running else 0))
        for f in fired:
            self.add_event(f.t_us, f.code, f.value, f.src, f.action)
        if self._capture is not None:
            dig |= F.DIG_CAPTURING
        rec = (mean, tuple([min(255, x // 100) for x in lo[:4]]),
               tuple([min(255, (x + 99) // 100) for x in hi[:4]]), rpm_tach, rpm_ign, pw, duty, dig, 0)
        i = self.n_rec % self._cap
        self._ring[i] = rec
        self._ring_t[i] = t_us
        self.n_rec += 1
        self.latest = (t_us, rec)
        for k in range(5):
            if lo[k] < self.live_min[k]:
                self.live_min[k] = lo[k]
        if self._capture is not None and self.n_rec - 1 >= self._capture[1] + v["post_s"] * 1000:
            self._finish_capture()
        if tick.n % 1000 == 0 and self._edges:
            horizon = t_us - self._cap * 1000
            if self._edges[0][0] < horizon:
                self._edges = [e for e in self._edges if e[0] >= horizon]

    def _finish_capture(self) -> None:
        trig, n_trig = self._capture
        self._capture = None
        v = self.cfg.values
        lo = max(0, n_trig - v["pre_s"] * 1000, self.n_rec - self._cap)
        idx = np.arange(lo, self.n_rec) % self._cap
        t = self._ring_t[idx].copy()
        edges = [e for e in self._edges if e[0] >= t[0]]
        ev = BlackboxEvent(
            event_no=self.event_no + 1, boot_no=self.boot_no, reason=trig.code,
            trigger_t_us=trig.t_us, trigger_value=trig.value, unix_us_at_t0=self.unix_us_at_t0,
            time_valid=self.time_valid, pre_ms=n_trig - lo, post_ms=v["post_s"] * 1000,
            fw="sim", device=self.device, tach_ppr=v["tach_ppr"], ign_ppr=v["ign_ppr"],
            config_text=self.cfg.dump(), t_us=t, samples=self._ring[idx].copy(),
            edge_t_us=np.array([e[0] for e in edges], dtype=np.int64),
            edge_ch=np.array([e[1] for e in edges], dtype=np.uint8),
            edge_level=np.array([e[2] for e in edges], dtype=np.uint8),
            events=[r for r in self.events if r.t_us >= t[0]])
        self.event_no += 1
        self.captures += 1
        self._last_capture_end = int(t[-1]) + 1000
        self.finished.append(write_event(self.event_path(self.event_no), ev))


# ---- the device on its USB port ---------------------------------------------

class SimDevice:
    """A whole black box: car, core and protocol. Thread-safe.

    Call ``advance(t)`` with seconds since power-up, ``rx(bytes)`` with what
    the laptop sent, and ``take_tx()`` for what the box sends back.
    """

    def __init__(self, car: Car | None = None, cfg: Config | None = None,
                 out_dir: Path | None = None, boot_us: int = 5_000_000, drift_ppm: float = 0.0):
        self.car = car or Car()
        self.core = CoreModel(cfg, out_dir, boot_us=boot_us)
        self.drift = 1.0 + drift_ppm * 1e-6
        self._lock = threading.RLock()
        self._deframer = F.Deframer()
        self._tx = bytearray()
        self._seq = 0
        self._n = 0                         # ticks done
        self._live_period_ms = 1000 // self.core.cfg["live_hz"]
        self._last_live = 0
        self._tx_ready = threading.Event()  # set when there is something to take
        self.connected = True               # False: the cable is "unplugged"

    # -- time --

    def now_us(self) -> int:
        return self.core.boot_us + self._n * 1000

    def advance(self, t_s: float) -> None:
        """Run the car and the box up to ``t_s`` seconds after power-up."""
        target = int(t_s * self.drift * 1000)
        with self._lock:
            while self._n < target:
                self._n += 1
                self.core.feed(self.car.tick(self._n))
                self._link_poll()

    def fail(self, kind: str) -> None:
        with self._lock:
            self.car.fail(kind, self._n / 1000.0)

    def restart(self) -> None:
        with self._lock:
            self.car.restart(self._n / 1000.0)

    def press_button(self) -> None:
        with self._lock:
            self.car.press_button(self._n / 1000.0)

    # -- link --

    def _send(self, ftype: int, payload: bytes = b"") -> None:
        if self.connected:
            self._tx += F.frame_encode(ftype, payload, self._seq)
            self._seq = (self._seq + 1) & 0xFF
            self._tx_ready.set()

    def _text(self, text: str) -> None:
        for i in range(0, len(text), 240):
            self._send(F.F_TEXT, text[i:i + 240].encode())

    def take_tx(self) -> bytes:
        with self._lock:
            out = bytes(self._tx)
            self._tx.clear()
            self._tx_ready.clear()
            return out

    def wait_tx(self, timeout: float) -> None:
        """Sleep until the box has something to send, but no longer than
        ``timeout`` and no longer than until its next live frame is due. A
        reply to a request written from another thread ends the wait at once."""
        with self._lock:
            period = self._live_period_ms
            due = (self._last_live + period - self._n) / (1000.0 * self.drift) if period else timeout
        self._tx_ready.wait(max(0.001, min(timeout, due)))

    def _state(self) -> int:
        c = self.core
        dig = c.latest[1][7] if c.latest else 0
        return (F.ST_SD_OK | (F.ST_CAPTURING if c.capturing else 0)
                | (F.ST_TIME_VALID if c.time_valid else 0)
                | (F.ST_RUNNING if dig & F.DIG_RUNNING else 0)
                | (F.ST_IG1_ON if dig & F.DIG_IG1_ON else 0))

    def _link_poll(self) -> None:
        c = self.core
        for rec, event_no, started in c.new_events:
            self._send(F.F_EVENT, F.EVENT_MSG.pack(rec.t_us, rec.code, rec.src, rec.action,
                                                   rec.value, event_no, 1 if started else 0))
        c.new_events.clear()
        if not self._live_period_ms or self._n - self._last_live < self._live_period_ms or not c.latest:
            return
        self._last_live = self._n
        t_us, rec = c.latest
        sample = np.array([rec], dtype=F.SAMPLE_DTYPE).tobytes()
        mins = [rec[0][k] if c.live_min[k] == 0xFFFF else c.live_min[k] for k in range(5)]
        c.live_min = [0xFFFF] * 5
        self._send(F.F_LIVE, F.LIVE.pack(t_us, sample, *mins, c.event_no, self._state(),
                                         c.last_reason, 0))

    def rx(self, data: bytes) -> None:
        with self._lock:
            if not self.connected:
                return
            for ftype, _seq, p in self._deframer.feed(data):
                self._handle(ftype, p)

    def _handle(self, ftype: int, p: bytes) -> None:
        c = self.core
        now = self.now_us()
        if ftype == F.F_GET_HELLO:
            self._send(F.F_HELLO, F.HELLO.pack(
                F.PROTO_VERSION, F.FILE_VERSION, b"sim", c.device.encode()[:11], F.SAMPLE_HZ,
                1000 // self._live_period_ms if self._live_period_ms else 0, c.boot_no,
                c.event_no, len(F.ANALOG), len(F.EDGE)))
        elif ftype == F.F_TIME_REQ and len(p) >= 8:
            self._send(F.F_TIME_RSP, F.TIME_RSP.pack(struct.unpack_from("<q", p)[0], now, now))
        elif ftype == F.F_TIME_SET and len(p) >= 8:
            c.unix_us_at_t0 = struct.unpack_from("<q", p)[0]
            if not c.time_valid:
                c.add_event(now, F.EV_TIME_SYNC)
            c.time_valid = True
        elif ftype == F.F_MARK:
            c.add_event(now, F.EV_HOST_MARK, struct.unpack_from("<i", p)[0] if len(p) >= 4 else 0)
            if c.cfg["host_mark_triggers"]:
                c.host_trigger()
        elif ftype == F.F_TRIGGER:
            c.host_trigger()
        elif ftype == F.F_SELFTEST:
            self._text("selftest core: PASS\nselftest sd: PASS (simulated)\nselftest: PASS\n")
        elif ftype == F.F_GET_CONFIG:
            self._text(c.cfg.dump() + "# end\n")
        elif ftype == F.F_SET_CONFIG:
            line = p.decode("ascii", "replace").strip()
            key, _, value = line.partition("=")
            try:
                c.cfg.set(key.strip(), value.strip())
                c.reload_rules()
                self._live_period_ms = 1000 // c.cfg["live_hz"]
                self._text(f"ok {line}\n")
            except ConfigError:
                self._text(f"error {line}\n")
        elif ftype == F.F_SAVE_CONFIG:
            (c.out_dir / "BLACKBOX.CFG").write_text(c.cfg.dump())
            self._text("ok saved\n")
        elif ftype == F.F_LIST_FILES:
            for no in range(c.event_no, max(0, c.event_no - 32), -1):
                path = c.event_path(no)
                if path.exists():
                    data = path.read_bytes()
                    self._send(F.F_FILE_INFO, F.FILE_INFO.pack(no, len(data), data[16], 1))
            self._send(F.F_FILE_INFO, F.FILE_INFO.pack(0xFFFFFFFF, 0, 0, 0))
        elif ftype == F.F_READ_FILE and len(p) >= F.READ_FILE.size:
            no, offset, want = F.READ_FILE.unpack_from(p)
            path = c.event_path(no)
            if path.exists():
                with open(path, "rb") as fh:
                    fh.seek(offset)
                    self._send(F.F_FILE_DATA, struct.pack("<II", no, offset)
                               + fh.read(min(want, F.FILE_CHUNK)))
            else:
                self._send(F.F_FILE_DATA, struct.pack("<II", no, 0xFFFFFFFF))
        elif ftype == F.F_LIVE_RATE and len(p) >= 2:
            hz = struct.unpack_from("<H", p)[0]
            self._live_period_ms = max(1, 1000 // min(hz, 500)) if hz else 0


def device_from_port(name: str, **kwargs) -> SimDevice:
    """``bbsim:`` -> healthy car; ``bbsim:igniter`` -> that fault after 20 s;
    ``bbsim:main_relay@8`` -> after 8 s."""
    spec = name[len(BBSIM_PREFIX):] if name.startswith(BBSIM_PREFIX) else name
    fault, _, at = spec.partition("@")
    return SimDevice(Car(fault or None, float(at) if at else 20.0), **kwargs)


class SimPort:
    """The simulator as a port object for ``BlackboxReader`` (no pty)."""

    def __init__(self, device: SimDevice | None = None, clock=time.monotonic):
        self.device = device or SimDevice()
        self.name = BBSIM_PREFIX
        self._clock = clock
        self._t0 = None

    def open(self) -> None:
        if self._t0 is None:
            self._t0 = self._clock()

    def close(self) -> None:
        pass

    def write(self, data: bytes) -> None:
        self.device.advance(self._clock() - self._t0)
        self.device.rx(data)

    def read(self, timeout: float) -> bytes:
        end = self._clock() + timeout
        while True:
            self.device.advance(self._clock() - self._t0)
            data = self.device.take_tx()
            now = self._clock()
            if data or now >= end:
                return data
            # like a blocking read on the real port: wake when a frame is due,
            # not every 2 ms (which cost more than the model itself)
            self.device.wait_tx(end - now)


HELP = "keys: m main relay  i igniter  f fuel pump  k key off  b battery dip  r restart  space button  q quit"


def serve(device: SimDevice, link_path: str = BBSIM_SYMLINK, keys: bool = True,
          run_for: float | None = None, out=sys.stdout) -> None:
    """Put the device on a pseudo-terminal until 'q', Ctrl+C or run_for."""
    import termios
    import tty

    master, slave = os.openpty()
    tty.setraw(slave)
    pts = os.ttyname(slave)
    if os.path.islink(link_path) or os.path.exists(link_path):
        os.unlink(link_path)
    os.symlink(pts, link_path)
    print(f"simulated black box on {link_path} -> {pts}", file=out)
    print(f"event files go to {device.core.out_dir}", file=out)
    stdin_fd = old_term = None
    if keys and sys.stdin.isatty():
        stdin_fd = sys.stdin.fileno()
        old_term = termios.tcgetattr(stdin_fd)
        tty.setcbreak(stdin_fd)
        print(HELP, file=out)
    out.flush()
    t0 = last_status = time.monotonic()
    actions = {"m": "main_relay", "i": "igniter", "f": "fuel_pump", "k": "key_off", "b": "battery"}
    try:
        while run_for is None or time.monotonic() - t0 < run_for:
            fds = [master] + ([stdin_fd] if stdin_fd is not None else [])
            ready, _, _ = select.select(fds, [], [], 0.004)
            now = time.monotonic()
            device.advance(now - t0)
            if stdin_fd in ready:
                key = os.read(stdin_fd, 1).decode(errors="ignore").lower()
                if key == "q":
                    break
                if key in actions:
                    device.fail(actions[key])
                    print(f"\n[{actions[key]}]", file=out)
                elif key == "r":
                    device.restart()
                    print("\n[restart]", file=out)
                elif key == " ":
                    device.press_button()
            if master in ready:
                device.rx(os.read(master, 1024))
            data = device.take_tx()
            if data:
                try:
                    os.write(master, data)
                except BlockingIOError:
                    pass
            if stdin_fd is not None and now - last_status > 0.5:
                last_status = now
                c = device.core
                rec = c.latest[1] if c.latest else None
                if rec:
                    print(f"\rrpm {rec[3]:5d}  batt {rec[0][0] / 1000:5.2f}  ecu+B {rec[0][1] / 1000:5.2f}  "
                          f"pump {rec[0][2] / 1000:5.2f}  events {c.event_no}  "
                          f"{'CAPTURING' if c.capturing else '         '}", end="", file=out, flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        if old_term is not None:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_term)
        try:
            if os.path.islink(link_path):
                os.unlink(link_path)
        except OSError:
            pass
        os.close(master)
        os.close(slave)
        print("\nsimulator stopped", file=out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hondalog-bbsim",
                                 description="Simulated black box on a pty.")
    ap.add_argument("--link", default=BBSIM_SYMLINK, help=f"symlink to create (default {BBSIM_SYMLINK})")
    ap.add_argument("--fault", choices=FAULTS, help="stall to inject")
    ap.add_argument("--fault-after", type=float, default=20.0, metavar="SEC")
    ap.add_argument("--restart-after", type=float, default=15.0,
                    help="seconds until the car is started again, 0 = stays dead")
    ap.add_argument("--out", help="directory that plays the SD card (default: a temp dir)")
    ap.add_argument("--pre", type=int, default=30, help="pre-trigger seconds")
    ap.add_argument("--run-for", type=float, default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    cfg = Config()
    cfg.set("pre_s", args.pre)
    car = Car(args.fault, args.fault_after, restart_after=args.restart_after or None)
    serve(SimDevice(car, cfg, Path(args.out) if args.out else None), args.link, run_for=args.run_for)
    return 0


if __name__ == "__main__":
    sys.exit(main())
