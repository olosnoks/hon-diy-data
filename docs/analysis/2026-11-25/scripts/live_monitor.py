"""Analyse the newest window of the live drive. Usage:
python live_monitor.py DB [--from WALL_UNIX] [--to WALL_UNIX] [--drive ID]
Prints a markdown section. State (last analysed wall time) in work/live_state.json.
"""
import sys, json, time, sqlite3, argparse
from pathlib import Path
import numpy as np
sys.path.insert(0, "/home/claude/hon-diy-data")
from hondalog.data.reader import open_log

WARM, COLD = 60.0, 50.0   # hysteresis thresholds for ECT state
STATE = Path("/home/claude/analysis/work/live_state.json")

ap = argparse.ArgumentParser()
ap.add_argument("db")
ap.add_argument("--from", dest="t_from", type=float)
ap.add_argument("--to", dest="t_to", type=float)
ap.add_argument("--drive", type=int)
ap.add_argument("--nostate", action="store_true")
a = ap.parse_args()

c = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
did = a.drive or c.execute("select max(id) from drives").fetchone()[0]
row = c.execute("select status,end_reason,heartbeat_at,stall_count,capture_count from drives where id=?", (did,)).fetchone()
evs = c.execute("select * from events where drive_id=? order by rowid", (did,)).fetchall() if \
    "drive_id" in [r[1] for r in c.execute("pragma table_info(events)")] else []
log = open_log(a.db, did)
E = log.meta["epoch_unix"]
hm = lambda t: time.strftime("%H:%M:%S", time.localtime(E + t))

def S(n):
    s = log.series(n)
    return s.t.astype(float), s.v.astype(float)

rt, rv = S("rpm")
st = json.loads(STATE.read_text()) if STATE.exists() else {}
if a.t_from is not None:
    t0 = a.t_from - E
elif st.get("drive") == did and not a.nostate:
    t0 = st["last_t"] - E - 15  # small overlap to catch transitions at the edge
else:
    t0 = rt[0]
t1 = (a.t_to - E) if a.t_to else rt[-1]

def win(n, lo=None, hi=None):
    t, v = S(n)
    lo = t0 if lo is None else lo; hi = t1 if hi is None else hi
    m = (t >= lo) & (t <= hi) & np.isfinite(v)
    return t[m], v[m]

def stats(n, lo=None, hi=None, fmt="{:.1f}"):
    t, v = win(n, lo, hi)
    if not len(v): return "n/a"
    return f"{fmt.format(v.min())}/{fmt.format(np.mean(v))}/{fmt.format(v.max())}"

def mean(n, lo, hi):
    t, v = win(n, lo, hi)
    return float(np.mean(v)) if len(v) else np.nan

out = []
p = out.append
now = time.time()
p(f"\n### Window {hm(t0)} – {hm(t1)} (drive {did}, snapshot age {now-(E+rt[-1]):.0f} s at analysis {time.strftime('%H:%M:%S')})\n")
p(f"- drive row: status={row[0]} end_reason={row[1]} heartbeat={time.strftime('%H:%M:%S', time.localtime(row[2])) if row[2] else None} stall_count={row[3]} capture_count={row[4]}")

# ---- ECT state + transitions
et, ev = win("ect", t0 - 30)
state = None; trans = []
for t, v in zip(et, ev):
    s = "warm" if v >= WARM else ("cold" if v <= COLD else state)
    if state is not None and s != state and t >= t0:
        trans.append((t, state, s, v))
    state = s
wt, wv = win("ect")
if len(wv):
    fw = np.mean(wv >= WARM) * 100; fc = np.mean(wv <= COLD) * 100
    p(f"- ECT min/mean/max {stats('ect', fmt='{:.0f}')} °C; time-share warm(≥60) {fw:.0f} %, false-cold(≤50) {fc:.0f} %; state at end: **{state}** ({wv[-1]:.0f} °C); samples {len(wv)}")
    warmv = wv[wv >= WARM]; coldv = wv[wv <= COLD]
    if len(warmv): p(f"  - warm readings {warmv.min():.0f}–{warmv.max():.0f} °C (median {np.median(warmv):.0f}); spec warm 80–93 °C (176–200 °F)")
    if len(coldv): p(f"  - false-cold readings {coldv.min():.0f}–{coldv.max():.0f} °C (median {np.median(coldv):.0f})")
    mid = wv[(wv > COLD) & (wv < WARM)]
    if len(mid): p(f"  - in-between readings (50–60 °C): {len(mid)} samples, {np.round(mid[:8]).astype(int).tolist()}")
p(f"- ECT transitions in window: **{len(trans)}**")
for t, a_, b, v in trans:
    pre_iacv = mean("iacv", t - 10, t); post = lambda n: mean(n, t, t + 10)
    _, r10 = win("rpm", t, t + 10)
    p(f"  - {hm(t)} {a_}→{b} (ECT {v:.0f} °C): 10 s after: RPM min {r10.min() if len(r10) else np.nan:.0f} mean {np.mean(r10) if len(r10) else np.nan:.0f} "
      f"(10 s before {mean('rpm', t-10, t):.0f}); IACV {pre_iacv:.1f}→{post('iacv'):.1f} %; ign {mean('ign', t-10, t):.1f}→{post('ign'):.1f}°; "
      f"inj {mean('inj', t-10, t):.2f}→{post('inj'):.2f} ms; STFT {mean('stft', t-10, t):.1f}→{post('stft'):.1f} %; MAP {mean('map', t-10, t):.0f}→{post('map'):.0f} kPa")

# ---- engine by ECT state (sample-and-hold ECT onto rpm grid)
if len(et):
    idx = np.searchsorted(et, rt, side="right") - 1
    m = (rt >= t0) & (rt <= t1) & (idx >= 0)
    ect_on = np.where(idx >= 0, ev[np.clip(idx, 0, None)], np.nan)
    for lab, cond in (("false-cold <=50", ect_on <= COLD), ("mid 60-77", (ect_on >= 60) & (ect_on < 78)), ("hot >=78", ect_on >= 78)):
        mm = m & cond
        if mm.sum() > 20:
            secs = mm.sum() / max(m.sum(), 1) * (t1 - t0)
            ia = [mean("iacv", x, x + 0.01) for x in []]
            def band(n):
                tt, vv = win(n); j = np.searchsorted(et, tt, side="right") - 1; ok = j >= 0
                e2 = ev[np.clip(j, 0, None)]
                c2 = {"false-cold <=50": e2 <= COLD, "mid 60-77": (e2 >= 60) & (e2 < 78), "hot >=78": e2 >= 78}[lab] & ok
                return np.mean(vv[c2]) if c2.any() else np.nan
            p(f"- ECT band {lab}: ~{secs:.0f} s; RPM mean {rv[mm].mean():.0f}, p5 {np.percentile(rv[mm],5):.0f}, min {rv[mm].min():.0f}; IACV {band('iacv'):.1f} %; ign {band('ign'):.1f}°; inj {band('inj'):.2f} ms; STFT {band('stft'):.1f} %; MAP {band('map'):.0f} kPa")

# ---- overall channels
p(f"- RPM min/mean/max {stats('rpm', fmt='{:.0f}')}; IACV {stats('iacv')} %; ign {stats('ign')}°; inj {stats('inj', fmt='{:.2f}')} ms; MAP {stats('map', fmt='{:.0f}')} kPa; TPS {stats('tps')} %")
p(f"- STFT {stats('stft')} %; LTFT {stats('ltft')} %; O2 {stats('o2', fmt='{:.2f}')} V; closed loop {mean('closed_loop', t0, t1)*100:.0f} % of samples")
p(f"- IAT {stats('iat', fmt='{:.0f}')} °C; batt {stats('batt', fmt='{:.2f}')} V; alt_fr {stats('alt_fr', fmt='{:.0f}')} %; ELD {stats('eld')} A")
for f in ("fan", "ac_clutch", "ac_sw", "mil", "brake", "vtec"):
    t, v = win(f)
    if len(v) and (f == "fan" or v.max() > 0.5):
        sw = int(np.sum(np.abs(np.diff(v)) > 0.5))
        p(f"  - {f}: on {np.mean(v>0.5)*100:.0f} % of samples, {sw} switches" + (f", last {hm(t[-1])}={int(v[-1])}" if sw else ""))
# electrical load steps (ELD rises > 5 A vs 5 s median before)
lt_, lv_ = win("eld")
steps = []
for i in range(len(lv_)):
    pre = lv_[(lt_ >= lt_[i] - 6) & (lt_ < lt_[i] - 1)]
    if len(pre) >= 3 and lv_[i] - np.median(pre) > 5 and (not steps or lt_[i] - steps[-1][0] > 10):
        steps.append((lt_[i], np.median(pre), lv_[i]))
for t, a0, a1 in steps:
    p(f"- ELD step {hm(t)}: {a0:.0f} → {a1:.0f} A; RPM 10 s before/after {mean('rpm', t-10, t):.0f}/{mean('rpm', t, t+10):.0f}; IACV {mean('iacv', t-10, t):.1f}→{mean('iacv', t, t+10):.1f} %")
# 1-min timeline
tl = []
for b in np.arange(t0, t1, 60):
    tl.append(f"{hm(b)[:5]} rpm{mean('rpm',b,b+60):.0f} ect{mean('ect',b,b+60):.0f} iacv{mean('iacv',b,b+60):.1f} ign{mean('ign',b,b+60):.1f} stft{mean('stft',b,b+60):+.1f} ltft{mean('ltft',b,b+60):+.1f} eld{mean('eld',b,b+60):.0f}")
p("- 1-min means:\n  ```\n  " + "\n  ".join(tl) + "\n  ```")
kt, kv = win("knock")
if len(kv): p(f"- knock V {stats('knock', fmt='{:.2f}')}")
vt, vv = win("vss")
if len(vv) and vv.max() > 0: p(f"- **VSS non-zero**: max {vv.max():.0f} km/h")

# ---- low RPM / stall
m = (rt >= t0) & (rt <= t1)
low = m & (rv < 600)
if low.any():
    # group into events
    ti = rt[low]; groups = np.split(ti, np.where(np.diff(ti) > 2)[0] + 1)
    p(f"- **RPM < 600: {len(groups)} episode(s)**")
    for g in groups[:10]:
        gm = (rt >= g[0]) & (rt <= g[-1])
        p(f"  - {hm(g[0])}–{hm(g[-1])}: min {rv[gm].min():.0f} rpm")
        # 30 s before at 2 s bins
        rows = []
        for b in np.arange(g[0] - 30, g[0] + 4, 2.0):
            rows.append(f"{hm(b)[3:]} r{mean('rpm',b,b+2):.0f} ect{mean('ect',b-1,b+2):.0f} iacv{mean('iacv',b-1,b+2):.1f} ign{mean('ign',b,b+2):.1f} inj{mean('inj',b,b+2):.2f} map{mean('map',b,b+2):.0f} stft{mean('stft',b-1,b+2):.1f} batt{mean('batt',b-1,b+2):.1f} eld{mean('eld',b-1,b+2):.0f}")
        p("    ```\n    " + "\n    ".join(rows) + "\n    ```")
else:
    p("- RPM < 600: none")

# ---- link / gaps
lt, lv = win("link")
if len(lv):
    drops = []; i = 0
    while i < len(lv):
        if lv[i] == 0:
            j = i
            while j < len(lv) and lv[j] == 0: j += 1
            end = lt[j] if j < len(lv) else t1
            drops.append((lt[i], end - lt[i])); i = j
        else: i += 1
    dd = np.array([x[1] for x in drops]) if drops else np.zeros(0)
    p(f"- link drops (link=0 episodes): {len(drops)} ({len(drops)/max((t1-t0)/60,1e-9):.2f}/min), >1 s: {(dd>1).sum()}, longest {dd.max() if len(dd) else 0:.2f} s")
    for t, x in drops:
        if x > 1: p(f"  - link loss {hm(t)} for {x:.1f} s")
d = np.diff(rt[m]); tm = rt[m][1:]
if len(d):
    dur = (rt[m][-1] - rt[m][0]) / 60
    g1 = d > 1.0; g05 = d > 0.5
    p(f"- data gaps (rpm stream): >0.5 s {g05.sum()}, >1 s {g1.sum()} ({g1.sum()/max(dur,1e-9):.2f}/min), >2 s {(d>2).sum()}, longest {d.max():.2f} s at {hm(tm[d.argmax()])}; effective rpm rate {len(d)/max(dur*60,1e-9):.1f} Hz")
    if g1.any():
        p("  - >1 s gaps at: " + ", ".join(f"{hm(t)}({x:.1f}s)" for t, x in list(zip(tm[g1], d[g1]))[:15]))

# ---- events / captures
if evs:
    newev = [e for e in evs if any(isinstance(x, float) and t0 <= x - 0 <= t1 for x in e)]
    p(f"- events table rows for drive: {len(evs)}; last: {[str(x)[:60] for x in evs[-1]]}")

print("\n".join(out))
if not a.nostate:
    STATE.write_text(json.dumps({"drive": did, "last_t": float(t1) + E, "state": state}))
