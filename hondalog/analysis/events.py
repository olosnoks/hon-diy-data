"""The moments of a drive worth a look, each with a short verdict: engine
deaths, link dropouts, key-offs, near-stalls, VTEC, harsh acceleration and
braking, hard cornering, knock retards and the driver's marks. Pure functions on the logged
channels; no Qt. The Events screen (F3) lists them, Replay (F4) ticks them on
its scrubber and steps through them.

The rules are those of docs/analysis/2026-11-26 (stall-forensics.md,
link-dropouts.md, drive-idle-control.md) and 2026-11-27/index-bytes.md:

- **Silence** = no rpm read for ``silence_s`` (3 s, the link-loss rule). What
  came after it decides what it was:
  - the ECU answered with the engine still running (rpm > 400 and below
    1.25 × the rpm before + 100; or the car was moving): **dropout**;
  - the next read is a restart (rpm < 300, cranking 0x27 = 10, key-on
    0x27 = 5, or an after-start flare): the engine was off in the gap. At a
    standstill with a steady idle before it that is a **key-off** (or a loss
    of ECU power: the log cannot tell them apart); moving, a **stall**;
  - the recording ended in the silence: standing, a **key-off**; moving, a
    dropout that never came back.
- **Engine died** (stall) = the rpm fell below 300 while the ECU kept
  answering, from a running engine. Never seen so far.
- **Near-stall** = rpm below 600 at a standstill (≤ 5 km/h), recovered. The
  verdict reads the throttle around the fall: "blip lifted at the bite",
  "bite during rev-hang", "throttle 0.6 s late", "no throttle, saved by
  rolling", "throttle closed at clutch bite".
- **VTEC** = the vtec flag on.
- **Harsh accel / brake** = |a| from vss ≥ 0.30 g for 0.3 s (tonight's
  hardest braking was 0.22 g).
- **Hard corner** = |lateral g| from the GPS heading rate × GPS speed ≥ 0.45 g
  (``hondalog.analysis.motion.lateral_accel``; a rough, GPS-derived figure).
- **Knock retard** = 0x27 (``ign_limit``, ignition correction in ¼° counts)
  falls by 5 or more from one read to the next with MAP > 44 kPa.
- **Mark** = the driver's MARK (Space).

    found = detect({"rpm": (t, v), "vss": (t, v), "tps": (t, v), ...}, markers=[(t, "MARK 1")],
                   t_end=log.t1)
    for e in found: print(e.t, e.label, e.summary)      # 1234.5 NEAR-STALL 440 rpm · blip lifted at the bite
    counts(found)                                       # {"near_stall": 6, "dropout": 3, ...}

Times are whatever the channels' time base is (session seconds from a log);
``key`` (kind + time to 0.1 s) identifies an event for notes and overrides.
The kind names are shared with the web app (web/src/ui/events/detect.ts), so
a note written in one is found by the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np

from .motion import G, Car, gear, gear_name, grid, hold, lateral_accel, longitudinal_accel, resample

#: in list order for the filter chips
KINDS = ("stall", "near_stall", "dropout", "key_off", "vtec", "harsh_accel", "harsh_brake", "corner", "knock",
         "mark")
LABELS = {"stall": "ENGINE DIED", "near_stall": "NEAR-STALL", "dropout": "DROPOUT", "key_off": "KEY OFF",
          "vtec": "VTEC", "harsh_accel": "HARSH ACCEL", "harsh_brake": "HARSH BRAKE",
          "corner": "HARD CORNER", "knock": "KNOCK RETARD", "mark": "MARK"}
#: chip text
CHIPS = {"stall": "Engine died", "near_stall": "Near-stall", "dropout": "Link dropout", "key_off": "Key off",
         "vtec": "VTEC", "harsh_accel": "Harsh accel", "harsh_brake": "Harsh brake", "corner": "Cornering",
         "knock": "Knock retard",
         "mark": "Marks"}
#: the verdicts a person can pick instead of the computed one, per kind
VERDICT_CHOICES = {
    "near_stall": ("throttle closed at clutch bite", "blip lifted at the bite", "bite during rev-hang",
                   "retry after a let-go bite", "throttle late", "no throttle, saved by rolling", "idle air too low", "electrical load step"),
    "dropout": ("engine ran, K-line dropout", "cable bumped", "cable unplugged", "engine stopped (key off)"),
    "key_off": ("owner key-off", "engine died (power loss)", "cable pulled, engine ran"),
    "stall": ("engine died", "owner key-off", "cable pulled, engine ran"),
    "vtec": ("VTEC on",),
    "harsh_accel": ("hard launch", "hard pull"),
    "harsh_brake": ("hard stop", "emergency stop"),
    "corner": ("hard corner", "GPS noise"),
    "knock": ("knock retard", "transient (tip-in)"),
    "mark": ("mark",),
}
#: markers the recorder writes itself: they are worked out again here, not listed as marks
_AUTO_MARKS = ("STALL", "LINK", "ALARM", "CLEAR", "DROPOUT", "TRIGGER", "BB ")


@dataclass(frozen=True)
class Rules:
    near_stall_rpm: float = 600.0
    standstill_kmh: float = 5.0
    near_stall_merge_s: float = 3.0
    silence_s: float = 3.0
    stall_rpm: float = 300.0
    harsh_g: float = 0.30
    harsh_min_s: float = 0.3
    corner_g: float = 0.45
    knock_drop: int = 5
    knock_map_kpa: float = 44.0
    knock_merge_s: float = 2.0


@dataclass
class DriveEvent:
    kind: str
    t: float                    # the moment (the minimum of a dip, the start of a silence)
    t_end: float                # where it ended (= t for an instant)
    summary: str                # one line for the list: "440 rpm · blip lifted at the bite"
    verdict: str                # the short reading: "blip lifted at the bite"
    why: str = ""               # the longer reading for the detail pane
    severity: str = "info"      # "alarm", "warn", "info"
    value: float = float("nan")  # min rpm, silence length, g, degrees, ...
    data: dict = field(default_factory=dict)
    note: str = ""              # a person's note (from the database)
    verdict_override: str = ""  # a person's verdict (from the database)

    @property
    def key(self) -> str:
        return f"{self.kind}@{self.t:.1f}"

    @property
    def label(self) -> str:
        return LABELS.get(self.kind, self.kind.upper())

    @property
    def shown_verdict(self) -> str:
        return self.verdict_override or self.verdict

    @property
    def title(self) -> str:
        """"Near-stall · 440 rpm" for the detail pane."""
        name = CHIPS.get(self.kind, self.kind)
        if self.kind == "near_stall":
            return f"Near-stall · {self.value:,.0f} rpm"
        if self.kind == "dropout":
            return f"Link dropout · {self.value:.1f} s silent"
        if self.kind in ("harsh_accel", "harsh_brake"):
            return f"{name} · {self.value:.2f} g"
        if self.kind == "corner":
            return f"Hard {'right' if self.value > 0 else 'left'} · {abs(self.value):.2f} g"
        if self.kind == "knock":
            return f"Knock retard · {self.value:.2f}°"
        if self.kind == "vtec":
            return f"VTEC · {self.value:.1f} s"
        return name


def counts(events: Sequence[DriveEvent]) -> dict[str, int]:
    out = {k: 0 for k in KINDS}
    for e in events:
        out[e.kind] = out.get(e.kind, 0) + 1
    return out


# ---- helpers ---------------------------------------------------------------

def _series(channels: Mapping, name: str):
    pair = channels.get(name)
    if pair is None:
        return None
    t, v = (np.asarray(x, dtype=float) for x in pair)
    ok = np.isfinite(t) & np.isfinite(v)
    if not ok.any():
        return None
    t, v = t[ok], v[ok]
    order = np.argsort(t, kind="stable")
    return t[order], v[order]


def _at(series, t: float, max_age: float = 3.0) -> float:
    if series is None:
        return float("nan")
    return float(hold(series[0], series[1], [t], max_age)[0])


def _window(series, t0: float, t1: float):
    if series is None:
        return np.zeros(0), np.zeros(0)
    i0, i1 = np.searchsorted(series[0], t0, side="left"), np.searchsorted(series[0], t1, side="right")
    return series[0][i0:i1], series[1][i0:i1]


def _runs(mask) -> list[tuple[int, int]]:
    m = np.concatenate([[False], np.asarray(mask, dtype=bool), [False]])
    d = np.diff(m.astype(int))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def throttle_closed(tps, rpm=None, vss=None) -> float:
    """The throttle sensor's closed reading (it is not 0 %: 7.8 % on this
    car): the low end of the readings at idle, standing."""
    if tps is None:
        return 0.0
    v = tps[1]
    if rpm is not None and vss is not None:
        r = hold(rpm[0], rpm[1], tps[0], 1.5)
        s = hold(vss[0], vss[1], tps[0], 1.5)
        idle = v[(r < 1200) & (s <= 1)]
        if len(idle) >= 10:
            return float(np.percentile(idle, 5))
    return float(np.percentile(v, 2))


def _fmt(x: float, unit: str = "", d: int = 0) -> str:
    return "?" if x != x else f"{x:,.{d}f}{unit}"


# ---- near-stalls and engine deaths -----------------------------------------

def near_stalls(channels: Mapping, rules: Rules = Rules()) -> list[DriveEvent]:
    rpm = _series(channels, "rpm")
    if rpm is None:
        return []
    vss, tps = _series(channels, "vss"), _series(channels, "tps")
    target, iac = _series(channels, "idle_target"), _series(channels, "iac_cmd")
    ect, eld = _series(channels, "ect"), _series(channels, "eld")
    closed = throttle_closed(tps, rpm, vss)
    t, v = rpm
    low = (v < rules.near_stall_rpm) & (v >= rules.stall_rpm)
    groups: list[list[int]] = []
    for s, e in _runs(low):
        if groups and t[s] - t[groups[-1][1] - 1] < rules.near_stall_merge_s:
            groups[-1][1] = e
        else:
            groups.append([s, e])
    out = []
    for s, e in groups:
        i = s + int(np.argmin(v[s:e]))
        tm, vm = float(t[i]), float(v[i])
        speed = _at(vss, tm)
        if not (speed <= rules.standstill_kmh):
            continue
        before_t, before_v = _window(rpm, tm - 3.0, tm)
        after_t, after_v = _window(rpm, tm, tm + 3.0)
        if not len(before_v) or before_v.min() < rules.stall_rpm or before_v.max() < rules.near_stall_rpm:
            continue                    # a start (cranking), not a dip from a running idle
        if not len(after_v) or after_v.min() < rules.stall_rpm or after_v.max() < rules.near_stall_rpm:
            continue                    # it did not come back: a death or a key-off, not a near-stall
        # the fall: from the highest rpm in the 2.5 s before the minimum
        wt, wv = _window(rpm, tm - 2.5, tm)
        k = int(np.flatnonzero(wv >= wv.max() - 10.0)[-1])      # the last moment at the top (plateaus)
        t_peak, r_peak = float(wt[k]), float(wv[k])
        tgt = _at(target, t_peak, 6.0)
        excess = r_peak - tgt if tgt == tgt else float("nan")
        # steepest drop over about 0.35 s
        ft, fv = _window(rpm, t_peak, tm)
        fall = 0.0
        for a in range(len(ft)):
            b = np.searchsorted(ft, ft[a] + 0.35)
            if b < len(ft) and ft[b] > ft[a]:
                fall = min(fall, (fv[b] - fv[a]) / (ft[b] - ft[a]))
        if fall == 0.0 and tm > t_peak:
            fall = (vm - r_peak) / (tm - t_peak)
        tp_t, tp_v = _window(tps, tm - 2.5, tm + 1.5)
        is_open = tp_v > closed + 1.5
        tps_peak = _at(tps, t_peak)
        tps_min = _at(tps, tm)
        open_at_fall = tps_peak == tps_peak and tps_peak > closed + 1.5
        lifted = bool(len(tp_t)) and bool((~is_open & (tp_t > t_peak) & (tp_t <= tm)).any())
        open_in_fall = bool((is_open & (tp_t > t_peak) & (tp_t <= tm)).any()) if len(tp_t) else False
        opened_after = tp_t[(tp_t > t_peak) & is_open] if len(tp_t) else np.zeros(0)
        rolled = bool(speed > 0.5 or (vss is not None and (_window(vss, tm, tm + 1.0)[1] > 0.5).any()))
        # a first bite given up just before: a dip under the target, then an overshoot above it
        pt, pv = _window(rpm, t_peak - 6.0, t_peak - 0.3)
        let_go = tgt == tgt and len(pv) > 0 and float(pv.min()) < tgt - 60 and excess >= 150
        if open_at_fall and lifted:
            verdict = "blip lifted at the bite"
        elif open_at_fall:
            verdict = "throttle open at the bite"
        elif let_go and not rolled and not open_in_fall:
            verdict = "retry after a let-go bite"
        elif rolled and not open_in_fall:
            verdict = "no throttle, saved by rolling"
        elif excess == excess and excess >= 250 and not open_in_fall:
            verdict = "bite during rev-hang"
        elif len(opened_after) and opened_after[0] <= tm + 1.0:
            verdict = f"throttle {opened_after[0] - t_peak:.1f} s late"
        else:
            verdict = "throttle closed at clutch bite"
        iac0, iac1 = _at(iac, t_peak, 2.0), _at(iac, tm + 0.6, 2.0)
        why = (f"RPM fell {_fmt(-fall)} rpm/s from {_fmt(r_peak)}"
               + (f" ({excess:+,.0f} over the {tgt:,.0f} target)" if excess == excess else "")
               + f" to {vm:,.0f}. Throttle {_fmt(tps_peak, ' %')} at the start of the fall, "
               f"{_fmt(tps_min, ' %')} at the bottom (closed reads {closed:.1f} %)"
               + (f"; IAC {iac0:.0f} → {iac1:.0f}" if iac0 == iac0 and iac1 == iac1 else "")
               + (f"; rolling {speed:.0f} km/h" if speed > 0.5 else "")
               + (f"; coolant {_at(ect, tm, 5):.0f} °C" if ect is not None else "")
               + (f", electrical load {_at(eld, tm, 5):.0f} A" if eld is not None else "") + ".")
        out.append(DriveEvent("near_stall", tm, float(t[e - 1]), f"{vm:,.0f} rpm · {verdict}", verdict, why,
                              "alarm" if vm < 500 else "warn", vm,
                              {"min_rpm": vm, "peak_rpm": r_peak, "t_fall": t_peak, "target": tgt,
                               "excess": excess, "fall_rpm_s": fall, "tps_fall": tps_peak, "tps_min": tps_min,
                               "tps_closed": closed, "kmh": speed}))
    if out:
        worst = min(out, key=lambda e: e.value)
        worst.data["closest"] = True
    return out


def engine_deaths(channels: Mapping, rules: Rules = Rules()) -> list[DriveEvent]:
    """The rpm fell below ``stall_rpm`` with the ECU still answering, from a running engine."""
    rpm = _series(channels, "rpm")
    if rpm is None:
        return []
    vss, batt = _series(channels, "vss"), _series(channels, "batt")
    t, v = rpm
    out = []
    for s, e in _runs(v < rules.stall_rpm):
        if s == 0:
            continue
        bt, bv = _window(rpm, t[s] - 3.0, t[s])
        if not len(bv) or bv.max() < 500 or t[s] - t[s - 1] > rules.silence_s:
            continue
        speed = _at(vss, float(t[s]))
        verdict = "engine died with the ECU answering"
        why = (f"RPM fell from {bv.max():,.0f} to {v[s]:,.0f} while the ECU kept answering"
               + (f", at {speed:.0f} km/h" if speed == speed else "")
               + (f"; battery {_at(batt, float(t[s]), 5):.1f} V" if batt is not None else "") + ".")
        out.append(DriveEvent("stall", float(t[s]), float(t[e - 1]), f"rpm {bv.max():,.0f} → {v[s]:,.0f} · {verdict}",
                              verdict, why, "alarm", float(v[s]), {"kmh": speed}))
    return out


# ---- silences: dropouts, key-offs, stalls in the gap -----------------------

def silences(channels: Mapping, t_end: float | None = None, rules: Rules = Rules()) -> list[DriveEvent]:
    rpm = _series(channels, "rpm")
    if rpm is None:
        return []
    vss, batt, corr = _series(channels, "vss"), _series(channels, "batt"), _series(channels, "ign_limit")
    t, v = rpm
    gaps = [(int(i), float(t[i]), float(t[i + 1])) for i in np.flatnonzero(np.diff(t) > rules.silence_s)]
    if t_end is not None and t_end - t[-1] > rules.silence_s:
        gaps.append((len(t) - 1, float(t[-1]), float("nan")))
    out = []
    for i, ta, tb in gaps:
        r0 = float(v[i])
        steady = _window(rpm, ta - 5.0, ta)[1]
        k0 = _at(vss, ta, 5.0)
        b0 = _at(batt, ta, 5.0)
        moving_before = k0 == k0 and k0 > rules.standstill_kmh
        volts = f", {b0:.1f} V" if b0 == b0 else ""
        if tb != tb:                    # the recording ended in the silence
            dur = (t_end or ta) - ta
            if moving_before:
                kind, verdict = "dropout", "lost while moving, never came back"
                summary = f"{verdict} · {k0:.0f} km/h"
            else:
                kind, verdict = "key_off", "engine off, recording ended"
                summary = f"steady {np.median(steady):,.0f} rpm{volts}" if len(steady) else verdict
            why = (f"The ECU went silent at {r0:,.0f} rpm{' and ' + format(k0, '.0f') + ' km/h' if k0 == k0 else ''}"
                   f"{volts} and the recording ended {dur:.0f} s later. A key-off and a loss of ECU power look "
                   "the same here.")
            out.append(DriveEvent(kind, ta, t_end if t_end is not None else ta, summary, verdict, why,
                                  "info" if kind == "key_off" else "warn", dur, {"rpm_before": r0, "kmh": k0}))
            continue
        dur = tb - ta
        j = i + 1
        r1 = float(v[j])
        first = _window(rpm, tb, tb + 2.0)[1]
        k1 = _at(vss, tb, 5.0)
        moving_after = k1 == k1 and k1 > rules.standstill_kmh
        code = _at(corr, tb, 2.0)
        restart = (r1 < rules.stall_rpm or code in (5.0, 10.0)
                   or (not moving_after and len(first) and first.max() > max(1000.0, 1.25 * r0 + 100)))
        ran = r1 > 400 and r1 < 1.25 * r0 + 100
        if (moving_before or moving_after) and r1 > 400:
            kind = "dropout"
            verdict = "engine ran"
            summary = f"{dur:.1f} s silent · moving {_fmt(k0)}→{_fmt(k1)} km/h, engine ran"
        elif ran and not restart:
            kind = "dropout"
            verdict = "engine ran"
            summary = (f"{dur:.0f} s silent at idle · engine ran" if dur >= 20 else f"{dur:.1f} s silent · engine ran") \
                + f" ({r0:,.0f}→{r1:,.0f} rpm)"
        elif moving_before:
            kind, verdict = "stall", "engine off in the gap while moving"
            summary = f"{dur:.1f} s silent · restarted at {r1:,.0f} rpm"
        else:
            kind, verdict = "key_off", "engine off in the gap"
            summary = f"steady {np.median(steady):,.0f} rpm{volts} · restarted {dur:.0f} s later" if len(steady) \
                else f"restarted {dur:.0f} s later"
        why = (f"No reply from the ECU for {dur:.1f} s. Before: {r0:,.0f} rpm"
               + (f", {k0:.0f} km/h" if k0 == k0 else "") + volts + f". After: {r1:,.0f} rpm"
               + (f", {k1:.0f} km/h" if k1 == k1 else "")
               + (". The next read is a restart (cranking, key-on or an after-start flare)" if restart else "")
               + ".")
        out.append(DriveEvent(kind, ta, tb, summary, verdict, why,
                              "alarm" if kind == "stall" else "warn" if kind == "dropout" else "info", dur,
                              {"rpm_before": r0, "rpm_after": r1, "kmh_before": k0, "kmh_after": k1,
                               "restart": bool(restart)}))
    return out


# ---- VTEC, harsh, knock, marks ---------------------------------------------

def vtec_runs(channels: Mapping) -> list[DriveEvent]:
    vt = _series(channels, "vtec")
    if vt is None:
        return []
    rpm, tps, vss = _series(channels, "rpm"), _series(channels, "tps"), _series(channels, "vss")
    t, v = vt
    out = []
    for s, e in _runs(v > 0.5):
        t0 = float(t[s])
        t1 = float(t[e]) if e < len(t) else float(t[e - 1]) + (float(np.median(np.diff(t))) if len(t) > 1 else 1.0)
        dur = max(t1 - t0, 0.0)
        r = _window(rpm, t0 - 0.5, t1)[1]
        rp = float(r.max()) if len(r) else float("nan")
        tp = _at(tps, t0)
        g = int(gear([_at(rpm, t0)], [_at(vss, t0)], 1.0, hold_s=0.0)[0]) if rpm is not None and vss is not None else 0
        thr = "WOT" if tp == tp and tp >= 80 else (f"{tp:.0f} %" if tp == tp else "")
        verdict = f"on {dur:.1f} s at ~{rp:,.0f} rpm" + (f", {thr}" if thr else "") + (f" {gear_name(g)}" if g else "")
        out.append(DriveEvent("vtec", t0, t1, verdict, "VTEC on",
                              f"The VTEC solenoid flag was on for {dur:.1f} s (the flag is read about once a "
                              f"second), peak {rp:,.0f} rpm.", "info", dur, {"rpm": rp, "tps": tp, "gear": g}))
    return out


def harsh(channels: Mapping, car: Car = Car(), rules: Rules = Rules()) -> list[DriveEvent]:
    vss = _series(channels, "vss")
    if vss is None or len(vss[0]) < 10:
        return []
    tg = grid(vss[0][0], vss[0][-1], 10.0)
    v = resample(vss[0], vss[1], tg)
    a = longitudinal_accel(v, 0.1, car)
    out = []
    lim = rules.harsh_g * G
    for sign, kind in ((1, "harsh_accel"), (-1, "harsh_brake")):
        for s, e in _runs(np.nan_to_num(sign * a) >= lim):
            if (e - s) * 0.1 < rules.harsh_min_s:
                continue
            i = s + int(np.argmax(sign * a[s:e]))
            peak = abs(float(a[i])) / G
            k0, k1 = float(v[s]), float(v[e - 1])
            verdict = "hard launch" if kind == "harsh_accel" and k0 < 10 else \
                "hard pull" if kind == "harsh_accel" else "hard stop"
            out.append(DriveEvent(kind, float(tg[i]), float(tg[e - 1]),
                                  f"{peak:.2f} g · {k0:.0f}→{k1:.0f} km/h", verdict,
                                  f"{'Acceleration' if sign > 0 else 'Deceleration'} of {peak:.2f} g "
                                  f"({peak * G:.1f} m/s², from vss over 1.5 s) between {k0:.0f} and {k1:.0f} km/h.",
                                  "warn", peak, {"kmh0": k0, "kmh1": k1}))
    return out


def corners(channels: Mapping, rules: Rules = Rules()) -> list[DriveEvent]:
    """Hard cornering: |lateral g| from the GPS heading rate past ``corner_g``,
    one event per run (the peak), runs closer than 3 s merged. Value: the
    signed peak in g (+ right)."""
    hd, sp = _series(channels, "gps_heading"), _series(channels, "gps_speed")
    if hd is None or sp is None or len(hd[0]) < 3:
        return []
    kmh = np.interp(hd[0], sp[0], sp[1])
    t, a = lateral_accel(hd[0], hd[1], kmh)
    g = a / G
    out: list[DriveEvent] = []
    i, n = 0, len(t)
    while i < n:
        if not (abs(g[i]) >= rules.corner_g):
            i += 1
            continue
        sign, j, peak = np.sign(g[i]), i, i
        while j + 1 < n and sign * g[j + 1] >= rules.corner_g and t[j + 1] - t[j] < 3.0:
            j += 1
            if abs(g[j]) > abs(g[peak]):
                peak = j
        pg, tp = float(g[peak]), float(t[peak])
        side = "right" if pg > 0 else "left"
        k = float(kmh[peak])
        out.append(DriveEvent("corner", tp, float(t[j]),
                              f"cornering {abs(pg):.2f} g to the {side} at {k:.0f} km/h", "hard corner",
                              f"Lateral acceleration from the GPS heading rate peaked at {abs(pg):.2f} g to the "
                              f"{side} at {k:.0f} km/h. GPS-derived: a rough figure.",
                              "warn", pg, {"kmh": k}))
        i = j + 1
    return out


def knock_retards(channels: Mapping, rules: Rules = Rules()) -> list[DriveEvent]:
    corr = _series(channels, "ign_limit")
    if corr is None or len(corr[0]) < 2:
        return []
    mp, rpm = _series(channels, "map"), _series(channels, "rpm")
    t, v = corr
    drop = v[:-1] - v[1:]
    m = hold(mp[0], mp[1], t[1:], 2.0) if mp is not None else np.full(len(t) - 1, np.nan)
    hits = np.flatnonzero((drop >= rules.knock_drop) & (m > rules.knock_map_kpa))
    out: list[DriveEvent] = []
    for j in hits:
        tj = float(t[j + 1])
        counts_ = int(drop[j])
        if out and tj - out[-1].t_end < rules.knock_merge_s:
            last = out[-1]
            last.t_end = tj
            last.data["counts"] += counts_
            last.value = last.data["counts"] / 4.0
            last.summary = f"timing pulled {last.value:.2f}° at {last.data['rpm']:,.0f} rpm, MAP {last.data['map']:.0f} kPa"
            continue
        r, mk = _at(rpm, tj, 2.0), float(m[j])
        deg = counts_ / 4.0
        out.append(DriveEvent("knock", tj, tj, f"timing pulled {deg:.2f}° at {r:,.0f} rpm, MAP {mk:.0f} kPa",
                              "knock retard",
                              f"The ignition correction (0x27, ¼° counts) fell from {v[j]:.0f} to {v[j + 1]:.0f} "
                              f"under load: the ECU pulled timing, most likely knock control (a reading of medium "
                              f"confidence; the byte is read about once a second).",
                              "warn", deg, {"counts": counts_, "rpm": r, "map": mk}))
    return out


def marks(markers: Sequence) -> list[DriveEvent]:
    out = []
    for t, text in markers or ():
        text = str(text or "MARK")
        if text.upper().startswith(_AUTO_MARKS):
            continue
        out.append(DriveEvent("mark", float(t), float(t), text, "mark", f"The driver marked this moment: {text}.",
                              "info", float("nan"), {"text": text}))
    return out


def detect(channels: Mapping, markers: Sequence = (), *, t_end: float | None = None, car: Car = Car(),
           rules: Rules = Rules()) -> list[DriveEvent]:
    """Every event of one drive, in time order. ``channels``: log channel
    name -> (t, v). ``t_end``: the end of the recording (to tell a key-off at
    the end from nothing)."""
    found = (engine_deaths(channels, rules) + silences(channels, t_end, rules) + near_stalls(channels, rules)
             + vtec_runs(channels) + harsh(channels, car, rules) + corners(channels, rules)
             + knock_retards(channels, rules) + marks(markers))
    return sorted(found, key=lambda e: (e.t, KINDS.index(e.kind) if e.kind in KINDS else 99))


def closest_call(events: Sequence[DriveEvent]) -> DriveEvent | None:
    """The deepest near-stall."""
    near = [e for e in events if e.kind == "near_stall"]
    return min(near, key=lambda e: e.value) if near else None
