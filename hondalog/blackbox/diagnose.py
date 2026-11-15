"""Read a capture and say what went first.

``timeline`` lists what each signal did around the stall with millisecond
times; ``diagnose`` maps the order to the likely culprit. The mapping is a
fixed table of electrical facts about this car, not a guess from statistics:

=========================================  ===================================
what went first                            points at
=========================================  ===================================
ECU +B, with IG1 and battery still up      PGM-FI main relay (or its feed/ground)
tach, with ignition trigger still pulsing  igniter (ICM) or coil
ignition trigger and tach together,        ECU stopped firing: ECU, or its
  ECU +B up                                CKP/TDC/CYP inputs in the distributor
pump feed, engine dies about a second on   pump half of the main relay, or the
                                           ECU's pump control line
IG1                                        key turned off, or the ignition switch
battery, everything follows                battery terminal, main fuse, ground
=========================================  ===================================
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import format as F
from .eventfile import BlackboxEvent

LOW_V = 6.0             # a rail counts as "down" below this
PULSE_GAP_MS = 250.0    # a pulse train counts as stopped after this much silence


@dataclass
class Step:
    t_ms: float         # relative to the first thing that went wrong
    signal: str
    what: str


@dataclass
class Verdict:
    cause: str                      # short key: "main_relay", "igniter", ...
    title: str
    confidence: str                 # "high", "medium", "low"
    explanation: str
    steps: list = field(default_factory=list)       # Step, in order
    notes: list = field(default_factory=list)

    def text(self) -> str:
        lines = [f"{self.title}  (confidence: {self.confidence})", self.explanation, ""]
        lines += [f"  {s.t_ms:+9.1f} ms  {s.signal:<9} {s.what}" for s in self.steps]
        lines += [f"note: {n}" for n in self.notes]
        return "\n".join(lines)


def _rail_events(ev: BlackboxEvent, name: str, t_from: int) -> list[tuple[int, bool]]:
    """Every time the rail went from up to below LOW_V after ``t_from``:
    (device_us, sustained). Uses the per-millisecond minimum, so a dropout
    shorter than a millisecond counts. Sustained = still down 20 ms later."""
    i = F.RAILS.index(name)
    sel = ev.t_us >= t_from
    t = ev.t_us[sel]
    lo = ev.samples["min_dv"][sel, i] / 10.0
    mean = ev.samples["mv"][sel, i] / 1000.0
    out = []
    up = False
    for k in range(len(t)):
        if not up:
            up = lo[k] >= LOW_V + 1.0
        elif lo[k] < LOW_V:
            later = mean[k + 20:k + 25]
            out.append((int(t[k]), bool(len(later)) and float(later.mean()) < LOW_V))
            up = False
    return out


def _pulse_stop(ev: BlackboxEvent, ch: str, t_from: int) -> int | None:
    """When the first pulse went missing: the last active edge before a long
    silence, plus one period (pulses are periodic, so the last one seen is up
    to a period *before* whatever stopped them)."""
    t, level = ev.edges(ch)
    t = t[level == 1]
    if len(t) < 3 or not len(ev.t_us):
        return None
    t_end = int(ev.t_us[-1])
    nxt = np.append(t[1:], t_end)
    late = np.flatnonzero((nxt - t > PULSE_GAP_MS * 1000) & (nxt > t_from))
    if not len(late):
        return None
    k = int(late[0])
    period = int(np.median(np.diff(t[max(0, k - 4):k + 1]))) if k >= 1 else 0
    return int(t[k]) + min(period, int(PULSE_GAP_MS * 1000))


def timeline(ev: BlackboxEvent) -> list[tuple[int, str, str]]:
    """(device_us, signal, what) for everything that fell or stopped, by time."""
    if not len(ev.t_us):
        return []
    run = np.flatnonzero(ev.samples["dig"] & F.DIG_RUNNING)
    t_from = int(ev.t_us[run[0]]) if len(run) else int(ev.t_us[0])
    items = []
    for name in F.RAILS:
        events = _rail_events(ev, name, t_from)
        glitches = [t for t, sustained in events if not sustained]
        drops = [t for t, sustained in events if sustained]
        if glitches:
            more = f" ({len(glitches)} times)" if len(glitches) > 1 else ""
            items.append((glitches[0], name, f"dipped below 6 V and came back{more}"))
        if drops:
            items.append((drops[0], name, "fell below 6 V"))
    for ch in F.EDGE:
        t = _pulse_stop(ev, ch, t_from)
        if t is not None:
            items.append((t, ch, "first missing pulse"))
    return sorted(items)


def diagnose(ev: BlackboxEvent) -> Verdict:
    items = timeline(ev)
    notes = list(ev.warnings)
    t0 = items[0][0] if items else 0
    steps = [Step((t - t0) / 1000.0, sig, what) for t, sig, what in items]
    drop = {sig: t for t, sig, what in items if what == "fell below 6 V"}
    glitch = {sig: t for t, sig, what in reversed(items) if what.startswith("dipped")}
    stop = {sig: t for t, sig, what in items if what == "first missing pulse"}
    running = (ev.samples["dig"] & F.DIG_RUNNING) != 0
    if running.any():
        low = float(ev.samples["min_dv"][running, F.A_VBAT].min()) / 10.0
        if low < 9.5:
            notes.append(f"battery went as low as {low:.1f} V with the engine running")
    for sig, t in glitch.items():
        notes.append(f"{sig} dropped out briefly {(t - t0) / 1000.0:+.1f} ms: an intermittent contact")

    def before(a: int | None, b: int | None, margin_ms: float = 3.0) -> bool:
        """a happened, and clearly before b (or b never happened)."""
        return a is not None and (b is None or a < b - margin_ms * 1000)

    ecub, ig1, vbat, pump = (drop.get(k) for k in ("ecub", "ig1", "vbat", "pump"))
    tach, ign, inj = (stop.get(k) for k in ("tach", "ign", "inj"))
    first_pulse_stop = min((t for t in (tach, ign) if t is not None), default=None)

    def v(cause, title, confidence, explanation):
        return Verdict(cause, title, confidence, explanation, steps, notes)

    if not drop and not stop:
        return v("none", "Nothing stopped in this capture", "high",
                 f"Triggered by: {ev.reason_text}. No rail stayed down and the pulses kept coming.")

    if vbat is not None and not before(ecub, vbat, 0.0) and not before(ig1, vbat, 0.0):
        return v("battery", "Battery supply dropped first", "medium",
                 "Battery voltage at the box collapsed before or together with everything else. "
                 "Look at the battery terminals, the main fuse and the engine/body grounds.")
    if ig1 is not None and not before(ecub, ig1, 0.0):
        return v("ig1", "IG1 dropped first", "medium",
                 "IG1 went away before ECU +B. That is either the key being turned off or the "
                 "ignition switch (or its wiring) losing contact. If you did not touch the key, "
                 "suspect the switch.")
    # "first missing pulse" is an estimate good to a fraction of a period, so
    # the pulses only count as having stopped first if they lead by 100 ms
    if ecub is not None and before(ecub, first_pulse_stop, -100.0):
        conf = "high" if ig1 is None and vbat is None else "medium"
        return v("main_relay", "ECU +B dropped while IG1 and battery stayed up", conf,
                 "The ECU lost its power feed with the ignition still on. That feed comes through "
                 "the PGM-FI main relay: suspect the relay (cracked solder joints), then its "
                 "connector and the ECU power wiring.")
    if before(tach, ign, 50.0):
        return v("igniter", "Tach stopped while the ECU kept sending ignition pulses", "high",
                 "The ECU was powered and still commanding spark, but the coil was no longer being "
                 "switched. Suspect the igniter (ICM) or the coil in the distributor, or their "
                 "wiring.")
    if pump is not None and before(pump, first_pulse_stop, 100.0):
        return v("fuel_pump", "Fuel pump feed dropped, the engine died after it", "high",
                 "The pump lost power with the ECU still up, and the engine ran on for a moment "
                 "before it starved. Suspect the pump half of the main relay or the ECU's pump "
                 "control line.")
    if tach is not None and ign is not None and ecub is None:
        return v("ecu", "Ignition trigger and tach stopped together, ECU still powered", "medium",
                 "The ECU had power but stopped commanding spark. Either the ECU itself, or it "
                 "lost its crank/cam signals (CKP/TDC/CYP sensors in the distributor). The K-line "
                 "log shows whether the ECU kept answering.")
    if inj is not None and before(inj, first_pulse_stop, 300.0):
        return v("fuel_cut", "Injector pulses stopped before the ignition did", "low",
                 "The ECU stopped fuelling first. On the overrun that is normal fuel cut; "
                 "otherwise look at what the ECU saw in the K-line log.")
    return v("unknown", f"No clear order ({ev.reason_text})", "low",
             "The signals do not match a known pattern. Look at the timeline and the plots.")
