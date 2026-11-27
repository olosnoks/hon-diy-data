"""F5, Power: a dyno from a logged pull.

Left, the pulls found automatically in the open drive (one gear, throttle
held, rpm rising), each with a quality badge. In the middle three tabs:

- **Curve**: torque and hp against rpm (pyqtgraph), the D16Y8's published
  curve dashed (127 hp at 6,600, 145 N·m at 5,500; approximate), the
  uncertainty band, the VTEC zone and the highest rpm logged.
- **Pull trace**: rpm, speed and throttle / MAP over the pull.
- **Time in cell**: the Map 3D screen (``screens/map3d.py``), and per
  RPM x MAP cell the ignition correction byte 0x27 (``ign_corr`` /
  ``ign_limit``) and how often it fell by 5 or more counts under load
  (timing pulled; docs/analysis/2026-11-27/index-bytes.md).

Right, the result, the inputs the result depends on (editable: mass, tyre,
drivetrain loss, road load) and Compare / Save as baseline / Export CSV.

The analysis is agent D3's ``hondalog.analysis.power``: ``detect_pulls(drive)
-> pulls`` and ``curve(pull, inputs) -> rpm, torque, hp, band``. Until that
module is there (or if it fails on a drive) a small fallback here does the
same job from rpm, vss and TPS, so the screen works on its own and in the demo.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter
from PySide6.QtWidgets import (QFileDialog, QFormLayout, QLineEdit, QMenu, QStackedWidget,
                               QVBoxLayout, QWidget)

from .. import adapters as ad
from .. import paint
from ..controls import Button, Card, Segment
from ..theme import T
from .common import TAB_H, TabStrip, form_style, label, restyle

try:                                            # agent D3's analysis (written in parallel)
    from ...analysis import power as _power     # type: ignore[attr-defined]
except Exception:                               # pragma: no cover - depends on D3's progress
    _power = None
try:
    from ...analysis import motion as _motion
except Exception:                               # pragma: no cover
    _motion = None

TABS = (("curve", "Curve"), ("trace", "Pull trace"), ("cells", "Time in cell"))
#: the D16Y8 (2000 Civic EX) as published, approximately: 145 N·m at 5,500, 127 hp at 6,600
if _power is not None and hasattr(_power, "D16Y8"):        # one reference for the whole app: D3's
    REF_RPM = np.asarray(_power.D16Y8.rpm, dtype=float)
    REF_TORQUE = np.asarray(_power.D16Y8.torque_nm, dtype=float)
else:
    REF_RPM = np.array([1000, 2000, 3000, 4000, 4800, 5500, 6000, 6600, 7000], dtype=float)
    REF_TORQUE = np.array([100, 120, 132, 138, 139, 145, 142, 137, 128], dtype=float)
HP_PER_NM_RPM = 1.0 / 7120.9                     # hp = N·m x rpm / 7120.9
REF_HP = REF_TORQUE * REF_RPM * HP_PER_NM_RPM
VTEC_ZONE = (5000.0, 5600.0)                     # rpm; the switch-over seen at ~5,400 (drive-bytes.md)
RPM_RANGE, TQ_RANGE, HP_RANGE = (1000.0, 7000.0), (0.0, 160.0), (0.0, 140.0)
GEAR_RPM_PER_KMH = {1: 133.6, 2: 76.8, 3: 49.7, 4: 36.0, 5: 27.7}
ORDINAL = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th"}
QUALITY_TEXT = {"full": "FULL THROTTLE", "part": "PART", "short": "TOO SHORT"}
QUALITY_TOKEN = {"full": "ok", "part": "unverified", "short": "fg2"}
IGN_IDS = ("ign_corr", "ign_limit", "x27")


def ref_torque(rpm) -> np.ndarray:
    return np.interp(np.asarray(rpm, dtype=float), REF_RPM, REF_TORQUE)


def ref_hp(rpm) -> np.ndarray:
    rpm = np.asarray(rpm, dtype=float)
    return ref_torque(rpm) * rpm * HP_PER_NM_RPM


# ---- inputs, pulls and curves as the screen uses them ----------------------

@dataclass
class Inputs:
    """What the curve depends on. Mass, tyre, drivetrain loss and road load
    are the owner's to change; grade is unknown unless typed."""
    mass_kg: float = 1195.0             # car + driver
    tyre_m: float = 0.287               # rolling radius (185/65R14)
    loss_pct: float = 10.0              # drivetrain loss
    road_a: float = 179.0               # N, rolling + constant drag
    road_b: float = 0.384               # N per (m/s)², aero
    grade_pct: float | None = None      # None: unknown (widens the band)

    def car(self):
        """The same as D3's ``motion.Car`` (or None without it)."""
        if _motion is None:
            return None
        return _motion.Car(mass_kg=self.mass_kg, tyre_m=self.tyre_m, eta=1.0 - self.loss_pct / 100.0,
                           road_a=self.road_a, road_b=self.road_b)

    def for_analysis(self):
        """The object D3's ``curve`` takes: its own inputs class when it has
        one, else a ``motion.Car``, else this."""
        if _power is not None:
            for name in ("Inputs", "PowerInputs", "DynoInputs"):
                cls = getattr(_power, name, None)
                if cls is not None:
                    try:
                        import dataclasses
                        keys = {f.name for f in dataclasses.fields(cls)}
                        mine = dict(mass_kg=self.mass_kg, tyre_m=self.tyre_m, loss_pct=self.loss_pct,
                                    loss=self.loss_pct / 100.0, eta=1.0 - self.loss_pct / 100.0,
                                    drivetrain_loss=self.loss_pct / 100.0, road_a=self.road_a,
                                    road_b=self.road_b, grade_pct=self.grade_pct,
                                    car=self.car())
                        return cls(**{k: v for k, v in mine.items() if k in keys})
                    except Exception:
                        break
        return self.car() or self


@dataclass
class Pull:
    """One pull as the list shows it, whatever the analysis returned."""
    t0: float
    t1: float
    gear: str = ""
    rpm_lo: float = 0.0
    rpm_hi: float = 0.0
    quality: str = "part"               # full | part | short
    tps: float = float("nan")           # %
    map_kpa: float = float("nan")
    detail: str = ""
    wall: str = ""                      # "23:03:07"
    source: object = None               # the analysis's own pull object

    @property
    def duration(self) -> float:
        return self.t1 - self.t0

    @property
    def usable(self) -> bool:
        return self.quality in ("full", "part")


@dataclass
class Curve:
    rpm: np.ndarray
    torque: np.ndarray
    hp: np.ndarray
    lo: np.ndarray                      # band on the torque, N·m
    hi: np.ndarray
    band_pct: float = 12.0
    label: str = ""

    @property
    def empty(self) -> bool:
        return len(self.rpm) < 2

    def results(self, grade_known: bool) -> list[tuple[str, str, str]]:
        """(label, value, note) for the result panel."""
        if self.empty:
            return [("Peak torque", "--", ""), ("Power", "--", ""), ("vs D16Y8", "--", ""),
                    ("Uncertainty", "--", "")]
        i, j = int(np.nanargmax(self.torque)), int(np.nanargmax(self.hp))
        at_end = j >= len(self.hp) - 2
        ratio = float(np.nanmean(self.hp / np.maximum(ref_hp(self.rpm), 1e-6)))
        hi_rpm = float(self.rpm[-1])
        return [("Peak torque", f"{self.torque[i]:.0f} N·m", f"at {self.rpm[i]:,.0f} rpm"),
                ("Power", f"{self.hp[j]:.0f} hp", f"at {self.rpm[j]:,.0f}" + (" (end of pull)" if at_end else "")),
                ("vs D16Y8", f"≈ {ratio * 100:.0f} %", f"below {math.ceil(hi_rpm / 100) * 100:,.0f} rpm"),
                ("Uncertainty", f"± {self.band_pct:.0f} %", "grade known" if grade_known else "grade unknown")]

    def to_json(self) -> str:
        return json.dumps({"rpm": self.rpm.tolist(), "torque": self.torque.tolist(), "hp": self.hp.tolist(),
                           "label": self.label})

    @classmethod
    def from_json(cls, text: str) -> "Curve | None":
        try:
            d = json.loads(text)
            rpm, tq, hp = (np.asarray(d[k], dtype=float) for k in ("rpm", "torque", "hp"))
        except (ValueError, KeyError, TypeError):
            return None
        return cls(rpm, tq, hp, tq, tq, 0.0, str(d.get("label", "")))


def _arr(x) -> np.ndarray:
    return np.asarray(x if x is not None else [], dtype=float).ravel()


def _norm_curve(result, label: str) -> Curve:
    """D3's ``curve`` result (a tuple or an object) -> Curve."""
    if isinstance(result, Curve):
        return result
    if isinstance(result, (tuple, list)):
        rpm, tq, hp, band = (list(result) + [None] * 4)[:4]
    else:
        rpm, tq, hp = (getattr(result, k, None) for k in ("rpm", "torque", "hp"))
        band = getattr(result, "band", None)
        if band is None and getattr(result, "lo", None) is not None:
            band = (result.lo, result.hi)
    rpm, tq, hp = _arr(rpm), _arr(tq), _arr(hp)
    n = min(len(rpm), len(tq), len(hp))
    rpm, tq, hp = rpm[:n], tq[:n], hp[:n]
    pct = 12.0
    if band is None:
        lo, hi = tq * 0.88, tq * 1.12
    elif np.isscalar(band):
        frac = float(band) / (100.0 if float(band) > 1.0 else 1.0)
        pct = frac * 100.0
        lo, hi = tq * (1 - frac), tq * (1 + frac)
    elif isinstance(band, (tuple, list)) and len(band) == 2 and np.ndim(band[0]) == 1:
        lo, hi = _arr(band[0])[:n], _arr(band[1])[:n]
        pct = float(np.nanmedian((hi - lo) / 2 / np.maximum(tq, 1e-6))) * 100.0 if n else pct
    else:
        half = _arr(band)[:n]
        lo, hi = tq - half, tq + half
        pct = float(np.nanmedian(half / np.maximum(tq, 1e-6))) * 100.0 if n else pct
    return Curve(rpm, tq, hp, lo, hi, pct, label)


def _norm_pull(p, wall0: datetime | None) -> Pull:
    if isinstance(p, Pull):
        return p
    get = (lambda k, d=None: p.get(k, d)) if isinstance(p, dict) else (lambda k, d=None: getattr(p, k, d))
    t0 = float(get("t0", get("start", 0.0)) or 0.0)
    t1 = float(get("t1", get("end", t0)) or t0)
    gear = get("gear", "")
    if isinstance(gear, (int, np.integer)):
        gear = ORDINAL.get(int(gear), str(gear))
    q = str(get("quality", "part")).lower()
    q = "full" if q.startswith("full") or q == "wot" else "short" if "short" in q else q
    q = q if q in QUALITY_TEXT else "part"
    rpm_lo = float(get("rpm_lo", get("rpm0", get("rpm_start", 0.0))) or 0.0)
    rpm_hi = float(get("rpm_hi", get("rpm1", get("rpm_end", 0.0))) or 0.0)
    tps = float(get("tps", get("tps_mean", float("nan"))) or float("nan"))
    kpa = get("map_kpa", get("map", float("nan")))
    kpa = float(np.nanmean(kpa)) if np.ndim(kpa) and np.size(kpa) else float(kpa if kpa is not None else "nan")
    wall = str(get("wall", "") or "")
    if not wall and wall0 is not None:
        wall = (wall0 + timedelta(seconds=t0)).strftime("%H:%M:%S")
    detail = str(get("detail", get("note", "")) or "")
    if not detail:
        bits = [f"{t1 - t0:.1f} s"]
        if np.isfinite(tps):
            bits.append(f"TPS {tps:.0f} %")
        if np.isfinite(kpa):
            bits.append(f"MAP {kpa:.0f} kPa")
        detail = " · ".join(bits)
    return Pull(t0, t1, str(gear), rpm_lo, rpm_hi, q, tps, kpa, detail, wall, p)


# ---- the fallback analysis (until hondalog.analysis.power is there) --------

def _series(view: ad.LogView, cid: str, t: np.ndarray) -> np.ndarray:
    tt, vv = view.t.get(cid), view.data.get(cid)
    if tt is None or vv is None or len(tt) < 2:
        return np.full(len(t), np.nan)
    return np.interp(t, np.asarray(tt, float), np.asarray(vv, float), left=np.nan, right=np.nan)


def _grid(view: ad.LogView) -> np.ndarray:
    tt = view.t.get("rpm")
    if tt is None or len(tt) < 2:
        return np.zeros(0)
    return np.arange(float(tt[0]), float(tt[-1]), 0.1)


def fallback_detect(view: ad.LogView) -> list[Pull]:
    """Stretches in one gear with the throttle held and rpm rising."""
    t = _grid(view)
    if len(t) < 20:
        return []
    rpm, vss, tps = (_series(view, c, t) for c in ("rpm", "vss", "tps"))
    kpa = _series(view, "map", t)
    open_ = np.nan_to_num(tps) >= 35.0
    rising = np.gradient(np.nan_to_num(rpm), 0.1) > 80.0
    moving = np.nan_to_num(vss) > 5.0
    on = open_ & rising & moving
    out: list[Pull] = []
    i, n = 0, len(t)
    while i < n:
        if not on[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and (on[j + 1] or (j + 3 < n and on[j + 3])):    # bridge one-read gaps
            j += 1
        dur = t[j] - t[i]
        if dur >= 1.0:
            seg = slice(i, j + 1)
            ratio = np.nanmedian(rpm[seg] / np.maximum(vss[seg], 1.0))
            g = min(GEAR_RPM_PER_KMH, key=lambda k: abs(GEAR_RPM_PER_KMH[k] - ratio))
            coupled = abs(GEAR_RPM_PER_KMH[g] - ratio) / GEAR_RPM_PER_KMH[g] < 0.08
            mean_tps, mean_map = float(np.nanmean(tps[seg])), float(np.nanmean(kpa[seg]))
            span = float(np.nanmax(rpm[seg]) - np.nanmin(rpm[seg]))
            if dur < 2.0 or span < 1000 or not coupled:
                q = "short"
            elif mean_tps >= 75.0 and span >= 1500:
                q = "full"
            else:
                q = "part"
            out.append(Pull(float(t[i]), float(t[j]), ORDINAL[g] if coupled else "slipping",
                            float(np.nanmin(rpm[seg])), float(np.nanmax(rpm[seg])), q, mean_tps, mean_map))
        i = j + 1
    return out


def fallback_curve(view: ad.LogView, pull: Pull, inputs: Inputs) -> Curve:
    """Force at the wheels = m·a + road load; engine power = wheel power / (1 - loss)."""
    empty = Curve(*(np.zeros(0),) * 5)
    trim = min(0.5, (pull.t1 - pull.t0) / 8)          # the ends: throttle and clutch still moving
    t = np.arange(pull.t0 - 1.0, pull.t1 + 1.0, 0.1)
    inner = (t >= pull.t0 + trim) & (t <= pull.t1 - trim)
    if inner.sum() < 8:
        return empty
    rpm, vss = _series(view, "rpm", t), _series(view, "vss", t)
    v = np.nan_to_num(vss) * 1.011 / 3.6
    k = 7
    kernel = np.ones(k) / k
    vs = np.convolve(np.pad(v, k // 2, mode="edge"), kernel, mode="valid")
    a = np.convolve(np.pad(np.gradient(v, 0.1), k // 2, mode="edge"), kernel, mode="valid")
    grade = (inputs.grade_pct or 0.0) / 100.0
    force = inputs.mass_kg * 1.04 * a + inputs.road_a + inputs.road_b * vs ** 2 + inputs.mass_kg * 9.81 * grade
    p_engine = force * vs / max(0.5, 1.0 - inputs.loss_pct / 100.0)
    omega = np.maximum(rpm, 1.0) * 2 * math.pi / 60.0
    tq = (p_engine / omega)[inner]
    r = rpm[inner]
    ok = np.isfinite(r) & np.isfinite(tq)
    r, tq = r[ok], tq[ok]
    if len(r) < 8 or r.max() - r.min() < 300:
        return empty
    order = np.argsort(r)
    r, tq = r[order], tq[order]
    grid = np.arange(math.ceil(r[0] / 100) * 100, r[-1], 100.0)
    if len(grid) < 3:
        return empty
    ct = np.interp(grid, r, tq)
    band = 0.12 if inputs.grade_pct is None else 0.06
    hp = ct * grid * HP_PER_NM_RPM
    return Curve(grid, ct, hp, ct * (1 - band), ct * (1 + band), band * 100.0)


# ---- the analysis, D3's when it is there ----------------------------------

def analysis_name() -> str:
    if _power is not None and (hasattr(_power, "find_pulls") or hasattr(_power, "detect_pulls")):
        return "hondalog.analysis.power"
    return "built-in fallback (hondalog.analysis.power not there)"


def _quality(duration: float, span: float, tps_min: float, tps_mean: float) -> str:
    if duration < 1.0 or span < 700:
        return "short"
    if tps_min >= 80.0 and span >= 1200:
        return "full"
    return "part" if tps_mean >= 35.0 else "short"


def _from_d3(p, wall0: datetime | None) -> Pull:
    """One of ``analysis.power.find_pulls``' pulls as the list shows it."""
    kpa = getattr(p, "map_kpa", None)
    kpa = float(np.nanmean(kpa)) if kpa is not None and np.size(kpa) and np.isfinite(kpa).any() else float("nan")
    span = float(p.rpm1 - p.rpm0)
    gear = ORDINAL.get(int(p.gear), str(p.gear))
    pull = Pull(float(p.t0), float(p.t1), gear, float(p.rpm0), float(p.rpm1),
                _quality(p.t1 - p.t0, span, float(p.tps_min), float(p.tps_mean)), float(p.tps_mean), kpa,
                source=p)
    return _norm_pull(pull.__dict__ | {"detail": "", "wall": ""}, wall0) if wall0 else         _norm_pull(pull.__dict__ | {"detail": ""}, None)


def _d3_find(view: ad.LogView, inputs: Inputs) -> list:
    """D3's ``find_pulls`` on a drive, part-throttle and short pulls included
    (the list shows them with their quality; only full-throttle ones are a dyno)."""
    need = ("rpm", "vss", "tps")
    if any(view.t.get(c) is None or len(view.t[c]) < 3 for c in need):
        return []
    has_map = view.t.get("map") is not None and len(view.t["map"]) > 1
    return _power.find_pulls(view.t["rpm"], view.data["rpm"], view.data["vss"], view.data["tps"],
                             view.data["map"] if has_map else None, t_vss=view.t["vss"], t_tps=view.t["tps"],
                             t_map=view.t["map"] if has_map else None, car=inputs.car(), min_tps=35.0,
                             min_s=1.0, grade_pct=inputs.grade_pct or 0.0)


def detect_pulls(view: ad.LogView, inputs: Inputs | None = None) -> list[Pull]:
    """The pulls of a drive: D3's analysis when it is there (``find_pulls``,
    or a ``detect_pulls`` if it grows one), else the fallback here."""
    inputs = inputs or Inputs()
    wall0 = _wall0(view)
    if _power is not None and hasattr(_power, "find_pulls") and _motion is not None:
        try:
            return [_from_d3(p, wall0) for p in _d3_find(view, inputs)]
        except Exception:
            pass
    if _power is not None and hasattr(_power, "detect_pulls"):
        try:
            return [_norm_pull(p, wall0) for p in _power.detect_pulls(view)]
        except Exception:
            pass
    pulls = fallback_detect(view)
    for p in pulls:
        if not p.wall and wall0 is not None:
            p.wall = (wall0 + timedelta(seconds=p.t0)).strftime("%H:%M:%S")
        if not p.detail:
            p.detail = _norm_pull(p.__dict__ | {"detail": ""}, None).detail
    return pulls


def make_curve(view: ad.LogView, pull: Pull, inputs: Inputs) -> Curve:
    """Torque and hp against rpm for one pull, with the inputs as they are now."""
    label = f"{pull.wall or f'{pull.t0:.0f} s'} pull"
    src = pull.source
    if src is not None and isinstance(getattr(src, "curve", None), list) and _power is not None:
        try:
            # the inputs may have changed since the pull was found: find it again with this car
            again = [p for p in _d3_find(view, inputs) if abs(p.t0 - src.t0) < 0.6]
            rows = np.asarray((again[0] if again else src).curve, dtype=float)
            if rows.ndim == 2 and len(rows) >= 2:
                rpm, tq, hp_, lo, hi = rows[:, 0], rows[:, 1], rows[:, 2], rows[:, 3], rows[:, 4]
                with np.errstate(invalid="ignore", divide="ignore"):
                    pct = float(np.nanmedian((hi - lo) / 2 / np.abs(tq))) * 100.0
                return Curve(rpm, tq, hp_, lo, hi, pct if np.isfinite(pct) else 12.0, label)
            return Curve(*(np.zeros(0),) * 5, label=label)
        except Exception:
            pass
    if _power is not None and hasattr(_power, "curve"):
        try:
            return _norm_curve(_power.curve(src if src is not None else pull, inputs.for_analysis()), label)
        except Exception:
            pass
    c = fallback_curve(view, pull, inputs)
    c.label = label
    return c


def _wall0(view: ad.LogView) -> datetime | None:
    for key in ("started", "start", "wall0", "t0_wall"):
        value = (view.meta or {}).get(key)
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)) and value > 1e8:
            return datetime.fromtimestamp(value)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                pass
    return None


def cell_stats(view: ad.LogView, rpm_axis, map_axis):
    """Per RPM x MAP cell: samples, mean ignition correction (0x27, as logged)
    and how often it fell by 5 counts or more with MAP above 44 kPa."""
    rpm_axis, map_axis = np.asarray(rpm_axis, float), np.asarray(map_axis, float)
    shape = (len(map_axis), len(rpm_axis))
    hits, mean, retard = np.zeros(shape), np.full(shape, np.nan), np.zeros(shape)
    cid = next((c for c in IGN_IDS if c in view.t and len(view.t[c])), None)
    if cid is None:
        return hits, mean, retard, ""
    t = np.asarray(view.t[cid], float)
    v = np.asarray(view.data[cid], float)
    rpm, kpa = _series(view, "rpm", t), _series(view, "map", t)
    spec = view.specs.get(cid)
    units = getattr(spec, "units", "") or ""
    drop = 1.25 if "°" in units or "deg" in units else 5.0
    ok = np.isfinite(rpm) & np.isfinite(kpa) & np.isfinite(v)
    ix = np.clip(np.searchsorted(rpm_axis, rpm, side="right") - 1, 0, len(rpm_axis) - 1)
    iy = np.clip(np.searchsorted(map_axis, kpa, side="right") - 1, 0, len(map_axis) - 1)
    sums = np.zeros(shape)
    dv = np.r_[0.0, np.diff(v)]
    for k in np.nonzero(ok)[0]:
        hits[iy[k], ix[k]] += 1
        sums[iy[k], ix[k]] += v[k]
        if -dv[k] >= drop and kpa[k] > 44:
            retard[iy[k], ix[k]] += 1
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(hits > 0, sums / np.maximum(hits, 1), np.nan)
    return hits, mean, retard, cid


# ---- the demo drive --------------------------------------------------------

def demo_drive() -> ad.LogView:
    """A drive with four pulls like the design's (simulated: the demo touches no log).
    The car is driven by 90 % of the reference torque at full throttle."""
    inp = Inputs()
    ratio = {g: GEAR_RPM_PER_KMH[g] * 3.6 / 1.011 * 2 * math.pi * inp.tyre_m / 60.0 for g in GEAR_RPM_PER_KMH}
    t_list, rpm_l, vss_l, tps_l, map_l, ign_l = [], [], [], [], [], []
    state = {"t": 0.0, "v": 0.0}

    def push(rpm, v, tps, kpa, ign):
        t_list.append(state["t"])
        rpm_l.append(rpm)
        vss_l.append(v * 3.6 / 1.011)
        tps_l.append(tps)
        map_l.append(kpa)
        ign_l.append(ign)
        state["t"] += 0.1

    def cruise(seconds, gear, kmh, tps=12.0, kpa=34.0):
        v = kmh * 1.011 / 3.6
        for _ in range(int(seconds * 10)):
            push(kmh * GEAR_RPM_PER_KMH[gear] + 8 * math.sin(state["t"]), v, tps, kpa, 4.0)
        state["v"] = v

    def pull(gear, rpm0, rpm1, scale, tps, kpa):
        v = rpm0 / GEAR_RPM_PER_KMH[gear] * 1.011 / 3.6
        ign = 20.0                              # 0x27 above 41 kPa: the remembered knock state
        while True:
            rpm = v * 3.6 / 1.011 * GEAR_RPM_PER_KMH[gear]
            if rpm >= rpm1:
                break
            tq = scale * float(ref_torque(rpm))
            f = tq * ratio[gear] * (1 - inp.loss_pct / 100) / inp.tyre_m - inp.road_a - inp.road_b * v * v
            v += f / (inp.mass_kg * 1.04) * 0.1
            ign = max(0.0, ign - 1.5) if rpm > 3500 and scale > 0.8 else min(26.0, ign + 0.07)
            push(v * 3.6 / 1.011 * GEAR_RPM_PER_KMH[gear], v, tps, kpa, ign)
        state["v"] = v

    cruise(20, 1, 6)
    pull(1, 1000, 2100, 0.55, 40, 60)          # a launch: too short
    cruise(40, 3, 50)
    pull(2, 2300, 5411, 0.90, 84, 98)          # the full-throttle 2nd-gear pull
    cruise(60, 4, 80)
    pull(2, 1900, 4600, 0.50, 46, 70)
    cruise(60, 4, 85)
    pull(3, 3100, 4700, 0.55, 52, 76)
    cruise(40, 3, 45)
    pull(3, 2000, 6500, 0.92, 88, 99)          # the pull the tip asks for: 3rd gear, 2,000 -> 6,500
    cruise(30, 5, 100, 18, 42)
    t = np.array(t_list)
    data = {"rpm": np.array(rpm_l), "vss": np.array(vss_l), "tps": np.array(tps_l), "map": np.array(map_l),
            "iat": np.full(len(t), 22.0), "baro": np.full(len(t), 100.3)}
    times = {k: t for k in data}
    times["ign_corr"], data["ign_corr"] = t[::10], np.round(np.array(ign_l)[::10])     # a slow byte: 1 Hz
    view = ad.LogView(None, title="demo drive (simulated)", note="simulated pulls",
                      meta={"started": "2026-11-26T22:51:09"}, t=times, data=data)
    return view


# ---- widgets ---------------------------------------------------------------

class NumberField(QLineEdit):
    """A number typed in a plain text field (checked, kept within lo..hi on Enter or leaving it)."""

    valueChanged = Signal(float)

    def __init__(self, lo: float, hi: float, decimals: int, value: float, parent=None):
        super().__init__(parent)
        self.lo, self.hi, self.decimals = lo, hi, decimals
        self._value = float(value)
        self.setFont(T.font(13, 700, mono=True))
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setText(self._fmt(self._value))
        self.editingFinished.connect(self._typed)

    def _fmt(self, v: float) -> str:
        return f"{v:.{self.decimals}f}"

    def value(self) -> float:
        return self._value

    def setValue(self, v: float) -> None:
        v = max(self.lo, min(self.hi, float(v)))
        self.setText(self._fmt(v))
        if v != self._value:
            self._value = v
            self.valueChanged.emit(v)

    def _typed(self) -> None:
        try:
            self.setValue(float(self.text().replace(",", ".")))
        except ValueError:
            self.setText(self._fmt(self._value))


class PullList(QWidget):
    """PULLS FOUND: one row per pull, the selected one marked by an accent bar."""

    picked = Signal(int)
    HEAD, ROW, FOOT = 92, 66, 64

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pulls: list[Pull] = []
        self.sel = -1
        self.compare = -1
        self.title = ""
        self.setCursor(Qt.PointingHandCursor)

    def set_pulls(self, pulls: list[Pull], sel: int) -> None:
        self.pulls, self.sel = pulls, sel
        self.update()

    def mousePressEvent(self, event) -> None:
        i = int((event.position().y() - self.HEAD) // self.ROW)
        if 0 <= i < len(self.pulls):
            self.picked.emit(i)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), T.bg)
        p.setPen(T.fg)
        p.setFont(T.font(13, 800, spacing=0.06))
        p.drawText(QRectF(12, 8, w - 24, 20), Qt.AlignVCenter | Qt.AlignLeft, "PULLS FOUND")
        body = T.font(12, 600)
        p.setFont(body)
        p.setPen(T.fg2)
        text = ("Found automatically: one gear, throttle held, rpm rising. Only full-throttle pulls "
                "make a curve.")
        for i, line in enumerate(paint.wrap(body, text, w - 24)[:3]):
            p.drawText(QRectF(12, 30 + i * 17, w - 24, 17), Qt.AlignVCenter | Qt.AlignLeft, line)
        p.fillRect(QRectF(0, self.HEAD - 2, w, 1), T.rule)
        mono, small = T.font(13, 700, mono=True), T.font(12, 600)
        badge, mid = T.font(12, 800), T.font(13, 600)
        room = max(0, int((h - self.HEAD - self.FOOT) // self.ROW))
        for i, pull in enumerate(self.pulls[:room]):
            y = self.HEAD + i * self.ROW
            r = QRectF(0, y, w, self.ROW)
            if i == self.sel:
                p.fillRect(r, T.sunk)
                p.fillRect(QRectF(0, y, 5, self.ROW), T.accent)
            elif i == self.compare:
                p.fillRect(QRectF(0, y, 5, self.ROW), T.series3)
            p.fillRect(QRectF(0, r.bottom() - 1, w, 1), T.line)
            p.setFont(mono)
            p.setPen(T.fg)
            p.drawText(QRectF(14, y + 6, w - 28, 18), Qt.AlignVCenter | Qt.AlignLeft, pull.wall or f"{pull.t0:.0f} s")
            p.setFont(badge)
            p.setPen(T.color(QUALITY_TOKEN[pull.quality]))
            p.drawText(QRectF(14, y + 6, w - 26, 18), Qt.AlignVCenter | Qt.AlignRight, QUALITY_TEXT[pull.quality])
            p.setFont(mid)
            p.setPen(T.fg)
            rng = f"{pull.rpm_lo:,.0f}–{pull.rpm_hi:,.0f} rpm"
            p.drawText(QRectF(14, y + 25, w - 28, 18), Qt.AlignVCenter | Qt.AlignLeft,
                       paint.elide(mid, f"{pull.gear} · {rng}" if pull.gear else rng, w - 28))
            p.setFont(small)
            p.setPen(T.fg2)
            p.drawText(QRectF(14, y + 43, w - 28, 18), Qt.AlignVCenter | Qt.AlignLeft,
                       paint.elide(small, pull.detail, w - 28))
        if not self.pulls:
            p.setFont(body)
            p.setPen(T.fg2)
            for i, line in enumerate(paint.wrap(body, "No pull in this drive. Open another drive above.", w - 24)):
                p.drawText(QRectF(12, self.HEAD + 10 + i * 17, w - 24, 17), Qt.AlignVCenter | Qt.AlignLeft, line)
        foot = QRectF(0, h - self.FOOT, w, self.FOOT)
        p.fillRect(foot, T.sunk)
        p.fillRect(QRectF(0, foot.y(), w, 1), T.rule)
        p.setFont(body)
        p.setPen(T.fg)
        tip = "For a full curve: 3rd gear, 2,000 → 6,500 rpm, then the same road the other way."
        for i, line in enumerate(paint.wrap(body, tip, w - 24)[:3]):
            p.drawText(QRectF(12, foot.y() + 8 + i * 17, w - 24, 17), Qt.AlignVCenter | Qt.AlignLeft, line)
        p.fillRect(QRectF(w - 2, 0, 2, h), T.rule)
        p.end()


class Legend(QWidget):
    """torque · hp · D16Y8 published (approx.) · compare · baseline."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[tuple[str, str, bool]] = []       # (token, text, dashed)
        self.setFixedHeight(TAB_H)

    def set_items(self, items) -> None:
        self.items = list(items)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        font = T.font(13, 600)
        p.setFont(font)
        x = float(self.width())
        for token, text, dashed in reversed(self.items):
            tw = paint.text_w(font, text)
            x -= tw
            p.setPen(T.fg)
            p.drawText(QRectF(x, 0, tw + 2, self.height()), Qt.AlignVCenter | Qt.AlignLeft, text)
            x -= 24
            y = self.height() / 2
            if dashed:
                p.setPen(T.cpen(T.color(token), 2, Qt.FlatCap, dash=(3.0, 2.0)))
                p.drawLine(int(x), int(y), int(x + 18), int(y))
            else:
                p.fillRect(QRectF(x, y - 1.5, 18, 3), T.color(token))
            x -= 16
        p.end()


class ResultCard(Card):
    def __init__(self, parent=None):
        super().__init__("RESULT", parent=parent)
        self.cells = Curve(*(np.zeros(0),) * 5).results(False)

    def set(self, title: str, cells) -> None:
        self.title, self.cells = title, cells
        self.update()

    def paint_body(self, p, x, y, w, h) -> None:
        cw, ch = (w - 8) / 2, (h - 8) / 2
        lab, val, note = T.font(12, 700), T.font(20, 700, mono=True), T.font(12, 600)
        for n, (name, value, text) in enumerate(self.cells[:4]):
            r, c = divmod(n, 2)
            box = QRectF(x + c * (cw + 8), y + r * (ch + 8), cw, ch)
            paint.frame(p, box, 1, T.line, T.panel)
            p.setFont(lab)
            p.setPen(T.fg)
            p.drawText(box.adjusted(8, 4, -6, 0), Qt.AlignTop | Qt.AlignLeft, name)
            p.setFont(val)
            p.drawText(box.adjusted(8, 18, -6, 0), Qt.AlignTop | Qt.AlignLeft, paint.elide(val, value, cw - 14))
            p.setFont(note)
            p.setPen(T.fg2)
            p.drawText(box.adjusted(8, 0, -6, -4), Qt.AlignBottom | Qt.AlignLeft, paint.elide(note, text, cw - 14))


def _axis_style(axis, label_text: str) -> None:
    axis.setPen(pg.mkPen(T.color("line"), width=1))
    axis.setTextPen(pg.mkPen(T.color("fg")))
    axis.setTickFont(T.font(12, 600, mono=True))
    axis.setLabel(label_text, color=T.tokens.fg)


class CurvePlot(QWidget):
    """Torque (left axis) and hp (right axis) against rpm."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pw = pg.PlotWidget(parent=self)
        self.pw.setFrameShape(self.pw.Shape.NoFrame)
        self.pw.setFocusPolicy(Qt.NoFocus)
        pi = self.pi = self.pw.getPlotItem()
        pi.setMenuEnabled(False)
        pi.hideButtons()
        pi.vb.setMouseEnabled(False, False)
        pi.showAxis("right")
        self.vb2 = pg.ViewBox(enableMouse=False, enableMenu=False)
        pi.scene().addItem(self.vb2)
        pi.getAxis("right").linkToView(self.vb2)
        self.vb2.setXLink(pi.vb)
        pi.vb.sigResized.connect(self._sync_views)
        pi.vb.setRange(xRange=RPM_RANGE, yRange=TQ_RANGE, padding=0)
        self.vb2.setRange(yRange=HP_RANGE, padding=0)
        pi.showGrid(x=True, y=True, alpha=0.25)
        self.vtec = pg.LinearRegionItem(VTEC_ZONE, movable=False)
        self.vtec_label = pg.TextItem("VTEC", anchor=(0.5, 0))
        self.maxline = pg.InfiniteLine(angle=90, movable=False,
                                       label="logged max {value:,.0f}", labelOpts={"position": 0.06})
        self.band_lo, self.band_hi = pg.PlotDataItem(), pg.PlotDataItem()
        self.band = pg.FillBetweenItem(self.band_lo, self.band_hi)
        self.ref_tq = pg.PlotDataItem(REF_RPM, REF_TORQUE)
        self.ref_hp = pg.PlotDataItem(REF_RPM, REF_HP)
        self.cmp_tq, self.cmp_hp = pg.PlotDataItem(), pg.PlotDataItem()
        self.base_tq, self.base_hp = pg.PlotDataItem(), pg.PlotDataItem()
        self.tq, self.hp = pg.PlotDataItem(antialias=True), pg.PlotDataItem(antialias=True)
        for item in (self.vtec, self.vtec_label, self.maxline, self.band_lo, self.band_hi, self.band,
                     self.ref_tq, self.cmp_tq, self.base_tq, self.tq):
            pi.addItem(item)
        for item in (self.ref_hp, self.cmp_hp, self.base_hp, self.hp):
            self.vb2.addItem(item)
        self.vtec_label.setPos((VTEC_ZONE[0] + VTEC_ZONE[1]) / 2, TQ_RANGE[1])
        self.maxline.hide()
        self.retheme()

    def _sync_views(self) -> None:
        self.vb2.setGeometry(self.pi.vb.sceneBoundingRect())
        self.vb2.linkedViewChanged(self.pi.vb, self.vb2.XAxis)

    def resizeEvent(self, _event) -> None:
        self.pw.setGeometry(self.rect().adjusted(1, 1, -1, -1))
        self._sync_views()

    def retheme(self) -> None:
        self.pw.setBackground(T.bg)
        _axis_style(self.pi.getAxis("left"), "torque N·m")
        _axis_style(self.pi.getAxis("right"), "hp")
        _axis_style(self.pi.getAxis("bottom"), "engine rpm")
        warn = QColor(T.warn)
        warn.setAlpha(60)
        self.vtec.setBrush(pg.mkBrush(warn))
        for line in self.vtec.lines:
            line.setPen(pg.mkPen(warn, width=0))
        self.vtec_label.setColor(T.fg)
        self.vtec_label.setFont(T.font(12, 700))
        self.maxline.setPen(pg.mkPen(T.color("alarm"), width=1.5, style=Qt.DashLine))
        self.maxline.label.setColor(T.alarm)
        self.maxline.label.setFont(T.font(12, 700))
        band = QColor(T.series1)
        band.setAlpha(36)
        self.band.setBrush(pg.mkBrush(band))
        self.band.setPen(pg.mkPen(None))
        self.band_lo.setPen(pg.mkPen(None))
        self.band_hi.setPen(pg.mkPen(None))
        dashed = pg.mkPen(T.color("rule"), width=2, style=Qt.DashLine)
        self.ref_tq.setPen(dashed)
        self.ref_hp.setPen(dashed)
        self.cmp_tq.setPen(pg.mkPen(T.color("series3"), width=2))
        self.cmp_hp.setPen(pg.mkPen(T.color("series3"), width=2, style=Qt.DotLine))
        self.base_tq.setPen(pg.mkPen(T.color("series4"), width=2))
        self.base_hp.setPen(pg.mkPen(T.color("series4"), width=2, style=Qt.DotLine))
        self.tq.setPen(pg.mkPen(T.color("series1"), width=3.5))
        self.hp.setPen(pg.mkPen(T.color("series2"), width=3.5))

    def set_curve(self, c: Curve | None) -> None:
        if c is None or c.empty:
            for item in (self.tq, self.hp, self.band_lo, self.band_hi):
                item.setData([], [])
            return
        self.tq.setData(c.rpm, c.torque)
        self.hp.setData(c.rpm, c.hp)
        self.band_lo.setData(c.rpm, c.lo)
        self.band_hi.setData(c.rpm, c.hi)

    def set_other(self, which: str, c: Curve | None) -> None:
        tq, hp = (self.cmp_tq, self.cmp_hp) if which == "compare" else (self.base_tq, self.base_hp)
        if c is None or c.empty:
            tq.setData([], [])
            hp.setData([], [])
        else:
            tq.setData(c.rpm, c.torque)
            hp.setData(c.rpm, c.hp)

    def set_logged_max(self, rpm: float | None) -> None:
        if rpm is None or not np.isfinite(rpm):
            self.maxline.hide()
            return
        self.maxline.show()
        self.maxline.setValue(rpm)
        self.maxline.label.valueChanged()       # pyqtgraph skips the text while the line is hidden


class TracePlot(QWidget):
    """rpm, speed and throttle / MAP over the selected pull, x linked."""

    ROWS = (("rpm", "rpm", "series1"), ("vss", "km/h", "series3"), ("tps", "TPS % · MAP kPa", "series4"))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.gv = pg.GraphicsLayoutWidget(parent=self)
        self.gv.setFrameShape(self.gv.Shape.NoFrame)
        self.gv.setFocusPolicy(Qt.NoFocus)
        self.plots, self.curves = [], {}
        first = None
        for i, (cid, title, token) in enumerate(self.ROWS):
            pl = self.gv.addPlot(row=i, col=0)
            pl.setMenuEnabled(False)
            pl.hideButtons()
            pl.vb.setMouseEnabled(False, False)
            pl.showGrid(x=True, y=True, alpha=0.25)
            if first is None:
                first = pl
            else:
                pl.setXLink(first)
            self.plots.append((pl, title))
            self.curves[cid] = pl.plot([], [])
            if cid == "tps":
                self.curves["map"] = pl.plot([], [])
        self.retheme()

    def resizeEvent(self, _event) -> None:
        self.gv.setGeometry(self.rect().adjusted(1, 1, -1, -1))

    def retheme(self) -> None:
        self.gv.setBackground(T.bg)
        for (pl, title), (cid, _, token) in zip(self.plots, self.ROWS):
            _axis_style(pl.getAxis("left"), title)
            _axis_style(pl.getAxis("bottom"), "s into the pull" if cid == "tps" else "")
            self.curves[cid].setPen(pg.mkPen(T.color(token), width=2.5))
        self.curves["map"].setPen(pg.mkPen(T.color("series5"), width=2))

    def set_pull(self, view: ad.LogView | None, pull: Pull | None) -> None:
        if view is None or pull is None:
            for c in self.curves.values():
                c.setData([], [])
            return
        t = np.arange(pull.t0 - 1.0, pull.t1 + 1.0, 0.1)
        for cid, curve in self.curves.items():
            curve.setData(t - pull.t0, _series(view, cid, t))


class CellGrid(QWidget):
    """A heat table over RPM x MAP: the mean ignition correction or the count
    of retard events in each cell. Colours from the theme's heat stops."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.values = np.zeros((0, 0))
        self.rpm_axis = np.zeros(0)
        self.map_axis = np.zeros(0)
        self.fmt = "{:.0f}"
        self.note = ""
        self.zero_blank = False

    def set_data(self, values, rpm_axis, map_axis, fmt: str, note: str, zero_blank: bool = False) -> None:
        self.values, self.rpm_axis, self.map_axis = np.asarray(values, float), rpm_axis, map_axis
        self.fmt, self.note, self.zero_blank = fmt, note, zero_blank
        self.update()

    def paintEvent(self, _event) -> None:
        from ..theme import heat_ramp
        p = QPainter(self)
        p.fillRect(self.rect(), T.bg)
        w, h = self.width(), self.height()
        v = self.values
        p.setFont(T.font(13, 600))
        p.setPen(T.fg2)
        p.drawText(QRectF(8, h - 26, w - 16, 22), Qt.AlignVCenter | Qt.AlignLeft,
                   paint.elide(T.font(13, 600), self.note, w - 16))
        if v.size == 0:
            p.end()
            return
        ny, nx = v.shape
        left, top, bottom = 56.0, 6.0, 52.0
        cw, ch = (w - left - 8) / nx, (h - top - bottom) / ny
        finite = v[np.isfinite(v)]
        lo, hi = (float(finite.min()), float(finite.max())) if finite.size else (0.0, 1.0)
        span = hi - lo if hi > lo else 1.0
        cols = heat_ramp(T.tokens, (np.nan_to_num(v, nan=lo) - lo) / span)
        mono = T.font(12, 700, mono=True)
        p.setFont(mono)
        show_text = cw >= 30 and ch >= 16
        for j in range(ny):
            y = top + (ny - 1 - j) * ch
            for i in range(nx):
                r = QRectF(left + i * cw, y, cw - 1, ch - 1)
                if not np.isfinite(v[j, i]) or (v[j, i] == 0 and self.zero_blank):
                    p.fillRect(r, T.nodata if not np.isfinite(v[j, i]) else T.panel)
                    continue
                rgb = cols[j, i]
                ground = QColor.fromRgbF(float(rgb[0]), float(rgb[1]), float(rgb[2]))
                p.fillRect(r, ground)
                if show_text:
                    p.setPen(T.heat_ink(ground))
                    p.drawText(r, Qt.AlignCenter, self.fmt.format(v[j, i]))
        p.setPen(T.fg)
        for j in range(0, ny, max(1, ny // 6)):
            p.drawText(QRectF(0, top + (ny - 1 - j) * ch, left - 6, ch), Qt.AlignVCenter | Qt.AlignRight,
                       f"{self.map_axis[j]:.0f}")
        for i in range(0, nx, max(1, nx // 8)):
            p.drawText(QRectF(left + i * cw - 10, h - bottom + 2, 60, 18), Qt.AlignVCenter | Qt.AlignLeft,
                       f"{self.rpm_axis[i] / 1000:.1f}k")
        p.drawText(QRectF(0, h - bottom + 20, left - 6, 18), Qt.AlignVCenter | Qt.AlignRight, "kPa")
        p.drawText(QRectF(left, h - bottom + 20, 200, 18), Qt.AlignVCenter | Qt.AlignLeft, "rpm")
        p.end()


class CellsPage(QWidget):
    """Time in cell: the Map 3D screen, or a per-cell table of byte 0x27."""

    MODES = (("map3d", "Time in cell (3D)"), ("ign", "Ign correction 0x27"), ("retard", "Timing pulled"))

    def __init__(self, session, settings, parent=None):
        super().__init__(parent)
        from .map3d import Map3DScreen
        self.session = session
        self.mode_seg = Segment(self.MODES, "map3d", parent=self)
        self.mode_seg.setFixedHeight(36)
        self.map3d = Map3DScreen(session, settings, parent=self)
        self.grid = CellGrid(self)
        self.grid.hide()
        self.mode_seg.picked.connect(self.set_mode)
        self.view: ad.LogView | None = None
        self.mode = "map3d"

    def set_view(self, view: ad.LogView | None) -> None:
        self.view = view
        if self.mode != "map3d":
            self.set_mode(self.mode)

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.mode_seg.set_current(mode)
        self.map3d.setVisible(mode == "map3d")
        self.grid.setVisible(mode != "map3d")
        if mode == "map3d":
            return
        obs = self.session.obsmap
        hits, mean, retard, cid = cell_stats(self.view, obs.rpm_axis, obs.map_axis) if self.view is not None \
            else (np.zeros(0), np.zeros(0), np.zeros(0), "")
        if not cid:
            self.grid.set_data(np.zeros((0, 0)), obs.rpm_axis, obs.map_axis, "{:.0f}",
                               "This drive has no ignition correction channel (0x27: ign_corr / ign_limit).")
        elif mode == "ign":
            self.grid.set_data(mean, obs.rpm_axis, obs.map_axis, "{:.0f}",
                               f"Mean {cid} per cell, as logged (0x27: 1/4° counts; 4 = neutral at light load). "
                               "Grey = no reads.")
        else:
            self.grid.set_data(np.where(hits > 0, retard, np.nan), obs.rpm_axis, obs.map_axis, "{:.0f}",
                               f"retard events per cell: {cid} fell by 5 counts or more above 44 kPa "
                               "(knock control pulling timing). Medium confidence, 1 Hz reads.", zero_blank=True)

    def resizeEvent(self, _event) -> None:
        w, h = self.width(), self.height()
        self.mode_seg.setGeometry(0, 0, min(w, 620), 36)
        self.map3d.setGeometry(0, 42, w, h - 42)
        self.grid.setGeometry(0, 42, w, h - 42)


class PowerScreen(QWidget):
    message = Signal(str)

    def __init__(self, session, settings: QSettings | None = None, replay=None, parent=None):
        super().__init__(parent)
        self.setObjectName("powerroot")
        self.session = session
        self.settings = settings if settings is not None else QSettings("hondalog", "hondalog")
        self.replay = replay
        self.view: ad.LogView | None = None
        self.pulls: list[Pull] = []
        self.sel = -1
        self.curve: Curve | None = None
        self.compare: Curve | None = None
        self.compare_index = -1
        self.baseline = Curve.from_json(str(self.settings.value("power/baseline", "")))
        self.inputs = self._load_inputs()
        self.page = "curve"

        self.pull_list = PullList(self)
        self.drive_btn = Button("Open drive…", height=36, parent=self)
        self.drive_btn.clicked.connect(self.drive_menu)
        self.tabs = TabStrip(TABS, "curve", self)
        self.tabs.picked.connect(self.show_page)
        self.legend = Legend(self)
        self.stack = QStackedWidget(self)
        self.curve_plot = CurvePlot()
        self.trace_plot = TracePlot()
        self.cells = CellsPage(session, self.settings)
        self.map3d = self.cells.map3d                   # the shell opens MAP3D here
        for page in (self.curve_plot, self.trace_plot, self.cells):
            self.stack.addWidget(page)
        self.result = ResultCard(self)
        self.inputs_box = QWidget(self)
        self.inputs_box.setObjectName("powerinputs")
        form = QFormLayout(self.inputs_box)
        form.setContentsMargins(12, 30, 12, 8)
        form.setVerticalSpacing(4)
        self.fields: dict[str, NumberField] = {}
        for key, text, lo, hi, dec in (
                ("mass_kg", "Car + driver, kg", 600, 2500, 0),
                ("tyre_m", "Tyre radius, m", 0.20, 0.40, 3),
                ("loss_pct", "Drivetrain loss, %", 0, 30, 1),
                ("road_a", "Road load rolling, N", 0, 600, 0),
                ("road_b", "Road load aero, N/(m/s)²", 0, 2, 3)):
            box = NumberField(lo, hi, dec, getattr(self.inputs, key))
            box.valueChanged.connect(lambda _v, k=key: self._input_changed(k))
            form.addRow(label(text, px=13), box)
            self.fields[key] = box
        self.facts = label("", dim=True, wrap=True, px=13)
        form.addRow(self.facts)
        self.compare_btn = Button("Compare with another pull", kind="primary", height=36, parent=self)
        self.baseline_btn = Button("Save as baseline", height=36, parent=self)
        self.export_btn = Button("Export CSV", height=36, parent=self)
        self.compare_btn.clicked.connect(self.compare_menu)
        self.baseline_btn.clicked.connect(self.save_baseline)
        self.export_btn.clicked.connect(self.export_dialog)
        self.pull_list.picked.connect(self.select)
        self.retheme()
        self.load_view(demo_drive() if session.demo else None)

    # ---- data ----

    def _load_inputs(self) -> Inputs:
        inp = Inputs()
        for key in ("mass_kg", "tyre_m", "loss_pct", "road_a", "road_b"):
            value = self.settings.value(f"power/{key}")
            if value not in (None, ""):
                try:
                    setattr(inp, key, float(value))
                except (TypeError, ValueError):
                    pass
        return inp

    def _input_changed(self, key: str) -> None:
        value = float(self.fields[key].value())
        setattr(self.inputs, key, value)
        self.settings.setValue(f"power/{key}", value)
        self.recompute()

    def load_view(self, view: ad.LogView | None) -> None:
        """Open a drive: find its pulls, pick the best one."""
        self.view = view
        self.pulls = detect_pulls(view, self.inputs) if view is not None else []
        full = [i for i, p in enumerate(self.pulls) if p.quality == "full"]
        best = max(full, key=lambda i: self.pulls[i].rpm_hi - self.pulls[i].rpm_lo) if full else             next((i for i, p in enumerate(self.pulls) if p.usable), -1)
        self.compare, self.compare_index = None, -1
        self.pull_list.compare = -1
        self.drive_btn.set_text(view.title if view is not None and view.title else "Open drive…")
        self.cells.set_view(view)
        rpm = view.data.get("rpm") if view is not None else None
        self.curve_plot.set_logged_max(float(np.nanmax(rpm)) if rpm is not None and len(rpm) else None)
        self.select(best)

    def select(self, index: int) -> None:
        self.sel = index if 0 <= index < len(self.pulls) else -1
        self.pull_list.set_pulls(self.pulls, self.sel)
        self.recompute()

    def selected(self) -> Pull | None:
        return self.pulls[self.sel] if self.sel >= 0 else None

    def recompute(self) -> None:
        pull = self.selected()
        self.curve = make_curve(self.view, pull, self.inputs) if pull is not None and self.view is not None else None
        if self.compare_index >= 0 and self.view is not None:
            self.compare = make_curve(self.view, self.pulls[self.compare_index], self.inputs)
        self.curve_plot.set_curve(self.curve)
        self.curve_plot.set_other("compare", self.compare)
        self.curve_plot.set_other("baseline", self.baseline)
        self.trace_plot.set_pull(self.view, pull)
        cells = (self.curve or Curve(*(np.zeros(0),) * 5)).results(self.inputs.grade_pct is not None)
        self.result.set(f"RESULT · {pull.wall} PULL" if pull is not None and pull.wall else "RESULT", cells)
        self._legend()
        self._facts()
        self.export_btn.setEnabled(self.curve is not None and not self.curve.empty)
        self.baseline_btn.setEnabled(self.export_btn.isEnabled())
        self.compare_btn.setEnabled(sum(1 for p in self.pulls if p.usable) >= 2)

    def _legend(self) -> None:
        items = [("series1", "torque", False), ("series2", "hp", False),
                 ("rule", "D16Y8 published (approx.)", True)]
        if self.compare is not None:
            items.append(("series3", f"compare {self.compare.label}", False))
        if self.baseline is not None:
            items.append(("series4", f"baseline {self.baseline.label}".strip(), False))
        self.legend.set_items(items if self.page == "curve" else [])

    def _facts(self) -> None:
        pull, view = self.selected(), self.view
        bits = []
        if pull is not None:
            bits.append(f"Gear (rpm ÷ speed): {pull.gear or '?'}" + (" ✓" if pull.gear[:1].isdigit() else ""))
        if view is not None:
            for cid, name, unit in (("iat", "Intake air", "°C"), ("baro", "Baro", "kPa")):
                v = view.data.get(cid)
                if v is not None and len(v):
                    bits.append(f"{name}: {float(np.nanmedian(v)):.1f} {unit}")
        bits.append("Road grade: " + ("unknown (±12 %)" if self.inputs.grade_pct is None
                                      else f"{self.inputs.grade_pct:.1f} %"))
        bits.append(f"Analysis: {analysis_name()}")
        self.facts.setText("\n".join(bits))

    # ---- actions ----

    def show_pull(self, pull, view: ad.LogView | None = None) -> bool:
        """Replay's "Open in Power": that drive (if given and not open here) and the
        pull that starts where ``pull`` does (its ``key`` or ``t0``), on the Curve tab.
        False when no pull here matches (the list stays as it was)."""
        if view is not None and view is not self.view:
            self.load_view(view)
        if pull is None:
            return False
        t0 = getattr(pull, "key", None)
        t0 = getattr(pull, "t0", None) if not isinstance(t0, (int, float)) else t0
        if t0 is None or not self.pulls:
            return False
        best = min(range(len(self.pulls)), key=lambda i: abs(self.pulls[i].t0 - float(t0)))
        if abs(self.pulls[best].t0 - float(t0)) > 1.0:
            return False
        self.select(best)
        self.show_page("curve")
        return True

    def show_page(self, name: str) -> None:
        """'curve' | 'trace' | 'cells' (and 'map3d', the shell's name for the cells tab)."""
        name = "cells" if name == "map3d" else name
        if name not in dict(TABS):
            return
        self.page = name
        self.tabs.set_current(name)
        self.stack.setCurrentIndex([k for k, _ in TABS].index(name))
        self._legend()
        self._place()

    def drive_menu(self) -> None:
        menu = QMenu(self)
        if self.session.demo:
            menu.addAction("Demo drive (simulated)", lambda: self.load_view(demo_drive()))
        logs = getattr(self.session, "logs", None)
        rows = []
        try:
            rows = [r for r in (logs.drives() if logs is not None else []) if r.kind == "drive"][:20]
        except Exception as exc:                   # a database the reader cannot open
            self.message.emit(f"Power: cannot list drives: {exc}")
        for row in rows:
            menu.addAction(f"{row.name}  {row.duration_s / 60:.0f} min", lambda r=row: self.open_drive(r.ref))
        if self.replay is not None and getattr(self.replay, "view", None) is not None:
            menu.addAction("The drive open in Replay", lambda: self.load_view(self.replay.view))
        if not menu.actions():
            menu.addAction("No drives recorded yet").setEnabled(False)
        menu.exec(QCursor.pos())

    def open_drive(self, ref) -> bool:
        try:
            view = self.session.logs.read(ref)
        except (OSError, ValueError) as exc:
            self.message.emit(f"Power: {exc}")
            return False
        self.load_view(view)
        self.message.emit(f"Power: {len(self.pulls)} pulls in {view.title or ref}")
        return True

    def compare_menu(self) -> None:
        menu = QMenu(self)
        for i, p in enumerate(self.pulls):
            if i != self.sel and p.usable:
                menu.addAction(f"{p.wall}  {p.gear} · {p.rpm_lo:,.0f}–{p.rpm_hi:,.0f}",
                               lambda k=i: self.set_compare(k))
        if self.compare is not None:
            menu.addSeparator()
            menu.addAction("Stop comparing", lambda: self.set_compare(-1))
        menu.exec(QCursor.pos())

    def set_compare(self, index: int) -> None:
        self.compare_index = index if 0 <= index < len(self.pulls) and index != self.sel else -1
        self.compare = None
        self.pull_list.compare = self.compare_index
        self.pull_list.update()
        self.recompute()

    def save_baseline(self) -> None:
        if self.curve is None or self.curve.empty:
            return
        self.baseline = Curve(self.curve.rpm, self.curve.torque, self.curve.hp, self.curve.lo, self.curve.hi,
                              self.curve.band_pct, self.curve.label)
        self.settings.setValue("power/baseline", self.baseline.to_json())
        self.recompute()
        self.message.emit(f"Power: {self.curve.label} saved as the baseline")

    def export_dialog(self) -> None:
        pull = self.selected()
        name = f"power_{(pull.wall if pull else 'pull').replace(':', '')}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Export the curve", name, "CSV (*.csv)")
        if path:
            self.export_csv(path)

    def export_csv(self, path) -> int:
        """rpm, torque, hp, the band and the D16Y8 reference at each point. Returns rows written."""
        c = self.curve
        if c is None or c.empty:
            return 0
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["rpm", "torque_nm", "hp", "torque_lo_nm", "torque_hi_nm", "d16y8_torque_nm", "d16y8_hp"])
            for row in zip(c.rpm, c.torque, c.hp, c.lo, c.hi, ref_torque(c.rpm), ref_hp(c.rpm)):
                w.writerow([f"{x:.1f}" for x in row])
        self.message.emit(f"Power: curve written to {path}")
        return len(c.rpm)

    # ---- the shell's interface ----

    def refresh(self, now: float) -> None:
        if self.page == "cells" and self.cells.mode == "map3d":
            self.map3d.refresh(now)

    def retheme(self) -> None:
        restyle(self, "powerroot")
        self.curve_plot.retheme()
        self.trace_plot.retheme()
        self.map3d.retheme()
        self.update()

    def key(self, event) -> bool:
        k = event.key()
        if k == Qt.Key_Tab:
            self.tabs.step(1)
            return True
        if self.page == "cells":
            return bool(self.map3d.key(event)) if self.cells.mode == "map3d" else False
        if k in (Qt.Key_Up, Qt.Key_Down) and self.pulls:
            self.select(max(0, min(len(self.pulls) - 1, self.sel + (1 if k == Qt.Key_Down else -1))))
            return True
        return False

    # ---- layout / paint ----

    def _place(self) -> None:
        w, h = self.width(), self.height()
        left, right = 290, 300
        wide = self.page == "cells"                     # Time in cell takes the result column's room too
        self.pull_list.setGeometry(0, 44, left, h - 44)
        self.drive_btn.setGeometry(8, 4, left - 16, 36)
        cx = left + 14
        cw = (w - cx - 14) if wide else (w - left - right - 28)
        self.tabs.setGeometry(cx, 8, self.tabs.sizeHint().width(), TAB_H)
        lx = cx + self.tabs.sizeHint().width() + 12
        self.legend.setGeometry(lx, 8, max(0, cx + cw - lx), TAB_H)
        self.stack.setGeometry(cx, 8 + TAB_H + 8, cw, h - 8 - TAB_H - 8 - 10)
        for widget in (self.result, self.inputs_box, self.compare_btn, self.baseline_btn, self.export_btn):
            widget.setVisible(not wide)
        rx = w - right
        self.result.setGeometry(rx + 2, 0, right - 2, 196)
        bh = 36
        by = h - 10 - 3 * bh - 2 * 6
        self.inputs_box.setGeometry(rx + 2, 196, right - 2, by - 196 - 6)
        for i, btn in enumerate((self.compare_btn, self.baseline_btn, self.export_btn)):
            btn.setGeometry(rx + 12, by + i * (bh + 6), right - 24, bh)

    def resizeEvent(self, _event) -> None:
        self._place()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), T.bg)
        if self.page != "cells":
            rx = self.width() - 300
            p.fillRect(QRectF(rx, 0, 2, self.height()), T.rule)
            p.fillRect(QRectF(rx, 196, 300, 1), T.rule)
            ib = self.inputs_box.geometry()
            p.fillRect(QRectF(rx, ib.bottom() + 3, 300, 1), T.rule)
            p.setFont(T.font(13, 800, spacing=0.06))
            p.setPen(T.fg)
            p.drawText(QRectF(rx + 14, ib.y() + 6, 260, 20), Qt.AlignVCenter | Qt.AlignLeft, "INPUTS")
        sg = self.stack.geometry()
        paint.frame(p, QRectF(sg).adjusted(-1, -1, 1, 1), 1, T.rule)
        p.end()
