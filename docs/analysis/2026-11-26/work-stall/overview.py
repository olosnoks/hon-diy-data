from ld import *
import datetime as dt
D = load_all()
f = lambda u: dt.datetime.fromtimestamp(u).strftime('%m-%d %H:%M:%S.%f')[:-4] if u==u and u else '-'
for did, d in D.items():
    s = d['s']; rt, rv = s['rpm']
    if len(rt) == 0: print(did, d['name'], 'no rpm'); continue
    print(did, d['name'], d['status'], d['end_reason'], 'honda' if d['honda'] else 'obd2', 'rpm n=%d' % len(rt), f(rt[0]), '->', f(rt[-1]), 'dur %.0f s' % (rt[-1]-rt[0]),
          'first rpm %s' % np.round(rv[:5]).tolist(), 'last rpm %s' % np.round(rv[-5:]).tolist(), 'min %.0f' % rv.min())
