from ld import *
import datetime as dt
D = load_all()
f = lambda u: dt.datetime.fromtimestamp(u).strftime('%m-%d %H:%M:%S.%f')[:-4]
rows = []
for did, d in D.items():
    s = d['s']; rt, rv = s['rpm']
    if len(rt) < 10: continue
    # crank time: first rpm > 400 after rpm < 300 at start
    t_ok = rt[0] + 30 if rv[:20].min() < 400 else rt[0]
    low = np.where((rv < 650) & (rt > t_ok))[0]
    if not len(low): continue
    # cluster
    groups = []; cur = [low[0]]
    for i in low[1:]:
        if rt[i] - rt[cur[-1]] < 5: cur.append(i)
        else: groups.append(cur); cur = [i]
    groups.append(cur)
    for g in groups:
        k = g[np.argmin(rv[g])]; tm = rt[k]
        pre = rv[(rt > tm - 10) & (rt < tm - 3)]
        r = dict(did=did, t=tm, rmin=rv[k], pre=np.median(pre) if len(pre) else np.nan)
        if d['honda']:
            et, ev = s[0x10]; c = ect_c(ev); m = (et > tm - 20) & (et <= tm)
            r['ect'] = '%.0f..%.0f' % (c[m].min(), c[m].max()) if m.any() else '-'
            r['ect_now'] = ect_c(at(s[0x10], tm))
            lt, lv = s[0x19]; m = (lt > tm - 5) & (lt <= tm + 2); r['eld'] = (77.06 - lv[m]/2.5371).max() if m.any() else np.nan
            it, iv = s[0x28]; r['iacv_pre'] = at(s[0x28], tm - 8)/2.55; r['iacv_now'] = at(s[0x28], tm)/2.55
            r['map'] = (at(s[0x12], tm))*0.716-5
            r['batt'] = at(s[0x17], tm)/10.45
            r['x29'] = at(s[0x29], tm) if 0x29 in s else np.nan
        rows.append(r)
rows.sort(key=lambda r: r['rmin'])
print('n dips (<650 rpm, excl. 30 s after crank):', len(rows))
for r in rows:
    print('#%d %s min %4.0f (from %4.0f)  ECT20s %-9s now %5.1f  ELD max %5.1f  IACV %4.1f->%4.1f  MAP %4.1f  batt %4.1f x29 %s' % (
        r['did'], f(r['t']), r['rmin'], r['pre'], r.get('ect', '-'), r.get('ect_now', np.nan), r.get('eld', np.nan), r.get('iacv_pre', np.nan), r.get('iacv_now', np.nan), r.get('map', np.nan), r.get('batt', np.nan), r.get('x29', np.nan)))
# overall rpm minimum per drive running
print()
for did, d in D.items():
    rt, rv = d['s']['rpm']
    if len(rt) < 10: continue
    t_ok = rt[0] + 30 if rv[:20].min() < 400 else rt[0]
    m = rt > t_ok
    print('#%d min running rpm %.0f at %s, p1 %.0f' % (did, rv[m].min(), f(rt[m][np.argmin(rv[m])]), np.percentile(rv[m], 1)))
