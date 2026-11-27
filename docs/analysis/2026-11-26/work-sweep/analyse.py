"""Characterise every byte of the 0x00-0xFF sweeps.

    python analyse.py work-sweep/car-XXXXXX-wide.csv > summary.txt
Writes <prefix>-bytes.csv (one line per address) next to the input.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import csv

KNOWN = {"rpm": None, "b10_ect": 0x10, "b11_iat": 0x11, "b12_map": 0x12, "b14_tps": 0x14,
         "b15_o2": 0x15, "b17_batt": 0x17, "b19_eld": 0x19, "b20_stft": 0x20, "b22_ltft": 0x22,
         "b24_inj": 0x24, "b26_ign": 0x26, "b28_iacv": 0x28, "b29_iac": 0x29}


def _rank(v):
    order = v.argsort(kind="mergesort")
    r = np.empty(len(v)); r[order] = np.arange(len(v))
    # average ties
    for val in np.unique(v):
        m = v == val
        r[m] = r[m].mean()
    return r


def spearman(a, b):
    ra = _rank(np.asarray(a)); rb = _rank(np.asarray(b))
    if ra.std() == 0 or rb.std() == 0:
        return np.nan
    return float(np.corrcoef(ra, rb)[0, 1])


def main(path: str) -> None:
    with open(path) as f:
        rows = list(csv.DictReader(f))
    cols = [f"b{i:02X}" for i in range(256)]
    B = np.array([[float(r[c]) if r[c] != "" else np.nan for c in cols] for r in rows])
    t = np.array([float(r["t"]) for r in rows])
    durs = np.array([float(r["dur_s"]) for r in rows])
    n = len(rows)
    known = {}
    known["rpm"] = (B[:, 0] * 256 + B[:, 1]) / 4
    for k, a in KNOWN.items():
        if a is not None:
            known[k] = B[:, a]
    print(f"{n} sweeps, {t[-1]-t[0]:.1f} s, mean sweep {durs.mean():.3f} s")
    for k, v in known.items():
        print(f"  {k:10s} min {np.nanmin(v):7.1f} max {np.nanmax(v):7.1f} mean {np.nanmean(v):7.2f}")
    out = []
    for a in range(256):
        x = B[:, a]
        ok = ~np.isnan(x)
        xs = x[ok].astype(int)
        rec = {"addr": f"0x{a:02X}", "replies": int(ok.sum())}
        if not ok.any():
            rec["kind"] = "no reply"
            out.append(rec)
            continue
        u = np.unique(xs)
        rec.update(min=int(xs.min()), max=int(xs.max()), mean=round(float(xs.mean()), 2),
                   std=round(float(xs.std()), 2), n_unique=len(u),
                   values=" ".join(str(v) for v in u[:12]) + (" ..." if len(u) > 12 else ""))
        diff = np.diff(xs)
        rec["frac_changed"] = round(float((diff != 0).mean()), 3) if len(diff) else 0
        anyor = np.bitwise_or.reduce(xs)
        changing = 0
        for bit in range(8):
            col = (xs >> bit) & 1
            if col.min() != col.max():
                changing |= 1 << bit
        rec["changing_bits"] = f"{changing:08b}"
        # counter test: diffs mod 256 constant non-zero?
        dm = diff % 256
        if len(dm) and (dm != 0).mean() > 0.5:
            vals, cnt = np.unique(dm, return_counts=True)
            rec["counter_like"] = f"step {vals[cnt.argmax()]} in {cnt.max()/len(dm):.0%}"
        if len(u) == 1:
            rec["kind"] = "constant"
        elif changing and bin(changing).count("1") <= 2 and len(u) <= 4:
            rec["kind"] = "bit field"
        elif len(u) <= 3:
            rec["kind"] = "few values"
        else:
            rec["kind"] = "continuous"
        # correlations (only if varying)
        if len(u) > 1:
            best, bestr = "", 0.0
            for k, v in known.items():
                m = ok & ~np.isnan(v)
                if (k.startswith("b") and int(k[1:3], 16) == a):
                    continue
                if np.std(v[m]) == 0:
                    continue
                r = spearman(x[m], v[m])
                if not np.isnan(r) and abs(r) > abs(bestr):
                    best, bestr = k, r
            rec["best_corr"] = best
            rec["spearman"] = round(bestr, 3)
        # copies: other addresses equal in >=98 % of sweeps (and not both constant)
        eq = []
        for b2 in range(256):
            if b2 == a:
                continue
            y = B[:, b2]
            m = ok & ~np.isnan(y)
            if m.sum() < 5:
                continue
            frac = float((x[m] == y[m]).mean())
            if frac >= 0.98 and (len(u) > 1):
                eq.append(f"0x{b2:02X}({frac:.0%})")
        rec["equal_to"] = " ".join(eq)
        out.append(rec)
    dst = Path(path).with_name(Path(path).name.replace("-wide.csv", "-bytes.csv"))
    keys = ["addr", "replies", "kind", "min", "max", "mean", "std", "n_unique", "frac_changed",
            "changing_bits", "counter_like", "best_corr", "spearman", "equal_to", "values"]
    with open(dst, "w", newline="") as f:
        w = csv.DictWriter(f, keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)
    for r in out:
        if r.get("kind") == "constant" and r.get("min") == 0:
            continue
        print(" | ".join(f"{k}={r.get(k, '')}" for k in keys if r.get(k, "") != ""))
    # 16-bit pairs: high byte steps when low byte wraps
    print("\n16-bit candidates (hi,lo) where lo wraps and hi changes in the same sweep:")
    for a in range(255):
        hi, lo = B[:, a], B[:, a + 1]
        m = ~np.isnan(hi) & ~np.isnan(lo)
        if m.sum() < 10 or np.nanstd(hi) == 0 or np.nanstd(lo) == 0:
            continue
        dh, dl = np.diff(hi[m]), np.diff(lo[m])
        wraps = np.abs(dl) > 128
        if wraps.sum() >= 2:
            agree = float(((np.sign(dh[wraps]) == -np.sign(dl[wraps])) & (dh[wraps] != 0)).mean())
            print(f"  0x{a:02X}:0x{a+1:02X}  lo wraps {int(wraps.sum())}, hi moves opposite in {agree:.0%}")


if __name__ == "__main__":
    main(sys.argv[1])

