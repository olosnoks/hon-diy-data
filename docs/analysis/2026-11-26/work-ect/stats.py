"""Per-drive ECT glitch statistics (edges >=15 C, steps >=4 C, false-cold/partial share), all drives."""
import lib, numpy as np, pickle
c = lib.con()
rows = []
allser = {}
for did, name, ep, st, tf, tl, dur in lib.drives(c):
    d = lib.load(did, names={'ect', 'vss', 'rpm', 'iat'}, c=c)
    if 'ect' not in d or 'ect_raw' not in d: continue
    t, e = d['ect']; r = d['ect_raw'][1]
    if did == 1: continue  # garbage in 0x08-0x11
    rpm = lib.at(d['rpm'], t) if 'rpm' in d else np.zeros_like(t)
    vss = lib.at(d['vss'], t) if 'vss' in d else np.zeros_like(t)
    run = rpm > 400
    dt = np.diff(t); de = np.diff(e)
    ok = (dt <= 3) & run[1:] & run[:-1]
    hrs = np.sum(np.minimum(dt, 3)[run[1:] & run[:-1]]) / 3600
    e15 = np.sum(ok & (np.abs(de) >= 15)); s4 = np.sum(ok & (np.abs(de) >= 4))
    cold = np.mean(e[run] < 55) if run.any() else 0; part = np.mean((e[run] >= 55) & (e[run] < 75)) if run.any() else 0
    mov = np.mean(vss[run] > 3) if run.any() else 0
    rows.append((did, name, hrs, e15, s4, cold, part, mov, np.median(e[run]) if run.any() else np.nan, np.percentile(e[run], 95) if run.any() else np.nan))
    allser[did] = dict(t=t, e=e, r=r, rpm=rpm, vss=vss, iat=lib.at(d['iat'], t) if 'iat' in d else None)
pickle.dump((rows, allser), open('ect_all.pkl', 'wb'))
print('drive                    h    e15  s4  e15/h  s4/h cold%% part%% moving%% medECT p95ECT')
for did, name, hrs, e15, s4, cold, part, mov, med, p95 in rows:
    if hrs < 0.01: continue
    print('%-24s %5.2f %4d %4d %6.1f %5.1f %5.1f %5.1f %6.1f %6.1f %6.1f' % (name, hrs, e15, s4, e15 / hrs, s4 / hrs, 100 * cold, 100 * part, 100 * mov, med, p95))
