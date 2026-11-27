from ld import *
import datetime as dt
D = load_all()
f = lambda u: dt.datetime.fromtimestamp(u).strftime('%m-%d %H:%M:%S')
EVT = {15: None, 20: None, 22: None, 27: None}
for k in EVT: EVT[k] = D[k]['s']['rpm'][0][-1]
print('== ECT glitch edges (|dECT| >= 15 C between consecutive reads <= 3 s apart) ==')
tot = {}
alledges = []
for did, d in D.items():
    s = d['s']
    if not d['honda'] or 0x10 not in s: continue
    t, v = s[0x10]; c = ect_c(v)
    dt_ = np.diff(t); dc = np.diff(c)
    m = (dt_ <= 3) & (np.abs(dc) >= 15)
    # running time: rpm > 400 coverage, gaps > 5 s excluded
    rt, rv = s['rpm']; g = np.diff(rt); run = g[(g < 5) & (rv[1:] > 400)].sum() / 3600
    day = dt.datetime.fromtimestamp(t[0]).strftime('%m-%d')
    edges = t[1:][m]; drops = (dc[m] < 0).sum()
    alledges += [(x, did, y) for x, y in zip(edges, dc[m])]
    cold = (c < 55).mean() * 100; part = ((c >= 55) & (c < 74)).mean() * 100
    tot.setdefault(day, [0, 0, 0]); tot[day][0] += m.sum(); tot[day][1] += run; tot[day][2] += drops
    print('#%d %s run %.2f h  edges %d (drops %d)  rate %.1f/h  false-cold %.1f %%  partial %.1f %%' % (did, d['name'], run, m.sum(), drops, m.sum()/run if run else 0, cold, part))
for k, (n, h, dr) in tot.items(): print('day %s: %d edges (%d drops) in %.2f h running (Honda logging) = %.1f edges/h, %.1f drops/h' % (k, n, dr, h, n/h, dr/h))
print('\n== ECT state and edges before each engine-off event ==')
allt = np.array([e[0] for e in alledges])
for did, te in EVT.items():
    s = D[did]['s']; c = ect_c(at(s[0x10], te))
    # stitch earlier drives: use all edges in all drives
    n10 = ((allt > te - 600) & (allt <= te)).sum(); n2 = ((allt > te - 120) & (allt <= te)).sum()
    last = allt[allt <= te].max() if (allt <= te).any() else np.nan
    print('%s ECT %.0f C; edges in last 10 min %d, last 2 min %d; last edge %.0f s before' % (f(te), c, n10, n2, te - last))
print('\n== 10 s minimum rpm in the 10 min before each event (stitched across drives) ==')
RT = np.concatenate([D[i]['s']['rpm'][0] for i in D]); RV = np.concatenate([D[i]['s']['rpm'][1] for i in D]); o = np.argsort(RT); RT, RV = RT[o], RV[o]
for did, te in EVT.items():
    mins = []
    for k in range(60, 0, -1):
        m = (RT > te - k*10) & (RT <= te - (k-1)*10)
        mins.append(int(RV[m].min()) if m.any() else -1)
    print(f(te), 'min per 10 s (oldest first):', mins)
    a = np.array([x for x in mins[:30] if x > 0]); b = np.array([x for x in mins[30:] if x > 0])
    print('   median of 10 s minima: -10..-5 min %d, -5..0 min %d; overall min %d' % (np.median(a), np.median(b), min(x for x in mins if x > 0)))
