from ld import *
import datetime as dt
D = load_all()
f = lambda u: dt.datetime.fromtimestamp(u).strftime('%m-%d %H:%M:%S.%f')[:-4] if u == u and u else '-'
def ser(s, k):
    return s.get(k, (np.array([]), np.array([])))
def window(s, k, t0, t1):
    t, v = ser(s, k); m = (t >= t0) & (t <= t1); return t[m], v[m]
def summ(s, t0, t1):
    out = {}
    t, v = window(s, 'rpm', t0, t1); out['rpm'] = (np.round(v.min()) if len(v) else None, np.round(v.max()) if len(v) else None, len(v))
    t, v = window(s, 0x08, t0, t1); out['starter'] = int(np.any(v.astype(int) & 1)) if len(v) else None
    t, v = window(s, 0x27, t0, t1); out['x27'] = sorted(set(v.astype(int).tolist())) if len(v) else None
    t, v = window(s, 0x17, t0, t1); out['batt'] = (round(v.min()/10.45, 1), round(v.max()/10.45, 1)) if len(v) else None
    t, v = window(s, 0x28, t0, t1); out['iacv'] = (round(v.min()/2.55, 1), round(v.max()/2.55, 1)) if len(v) else None
    t, v = window(s, 0x10, t0, t1); out['ect'] = (round(float(ect_c(v).min())), round(float(ect_c(v).max()))) if len(v) else None
    t, v = window(s, 0x0B, t0, t1); out['mil'] = int(np.any(v.astype(int) & 32)) if len(v) else None
    return out
ids = list(D)
print('=== drive boundaries ===')
for i, did in enumerate(ids):
    d = D[did]; s = d['s']; rt, rv = s['rpm']
    if not len(rt): continue
    end = rt[-1]
    nxt = None
    for j in ids[i+1:]:
        if len(D[j]['s']['rpm'][0]): nxt = j; break
    print('\n#%d %s end %s (%s/%s) last-30s %s' % (did, d['name'], f(end), d['status'], d['end_reason'], summ(s, end-30, end)))
    print('   last 2 s rpm:', np.round(window(s, 'rpm', end-2, end)[1]).tolist())
    if nxt:
        s2 = D[nxt]['s']; st = s2['rpm'][0][0]
        print('   -> next #%d starts %s gap %.1f s; first 10 s %s' % (nxt, f(st), st-end, summ(s2, st, st+10)))
        print('   first rpm:', np.round(s2['rpm'][1][:12]).tolist())
print('\n=== in-drive rpm gaps > 1.0 s ===')
for did, d in D.items():
    s = d['s']; rt, rv = s['rpm']
    if len(rt) < 2: continue
    g = np.diff(rt); idx = np.where(g > 1.0)[0]
    for k in idx:
        a, b = rt[k], rt[k+1]
        print('#%d %s gap %.1f s  rpm before %s after %s  after10s %s' % (did, f(a), b-a, np.round(rv[max(0,k-3):k+1]).tolist(), np.round(rv[k+1:k+4]).tolist(), summ(s, b, b+10) if d['honda'] else ''))
print('\n=== rpm < 300 with reads continuing ===')
for did, d in D.items():
    rt, rv = d['s']['rpm']
    m = np.where(rv < 300)[0]
    if len(m): print('#%d n=%d first %s last %s' % (did, len(m), f(rt[m[0]]), f(rt[m[-1]])))
print('\n=== starter bit / x27 in (5,10) / batt < 13.0 V with rpm>300 ===')
for did, d in D.items():
    s = d['s']
    if not d['honda']: continue
    t, v = ser(s, 0x08); m = (v.astype(int) & 1) > 0
    if m.any(): print('#%d starter set n=%d %s..%s' % (did, m.sum(), f(t[m][0]), f(t[m][-1])))
    t, v = ser(s, 0x27); m = np.isin(v.astype(int), [5, 10]) | (v > 10)
    if m.any(): print('#%d x27 in 5/10/>10 n=%d %s..%s values %s' % (did, m.sum(), f(t[m][0]), f(t[m][-1]), sorted(set(v[m].astype(int)))))
    t, v = ser(s, 0x17); r = at(s['rpm'], t); m = (v/10.45 < 13.0) & (r > 300)
    if m.any():
        print('#%d batt<13.0 running n=%d' % (did, m.sum()), [(f(a), round(b/10.45,2), round(c)) for a, b, c in zip(t[m][:8], v[m][:8], r[m][:8])])
