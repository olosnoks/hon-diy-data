"""Rule: 810 state on once time since last reset >= tau; reset = engine start, rpm > 1000 (off-idle or fast idle), accepted cool ECT reading (56..99 raw)."""
import numpy as np, pickle
from common import wall, wday, runs, idle
T = pickle.load(open('T2.pkl', 'rb')); R = pickle.load(open('resets.pkl', 'rb'))
def keep(k):
    parts = k.split('+')
    for p in parts:
        if p.startswith('start') or p.startswith('rev'): return True
        if p.startswith('ect') and int(p[3:]) < 100: return True
    return False
Rt = np.array([x[0] for x in R if keep(x[1])])
t = T['t']
j = np.searchsorted(Rt, t, side='right') - 1
tsr = np.where(j >= 0, t - Rt[np.clip(j, 0, None)], np.inf)
T['tsr'] = tsr
er = T['b10']
ok = idle & (er <= 44) & (T['ect_max60'] <= 46) & (T['t_since_start'] > 30) & ~T['fan_on'] & (T['t_since_fan_off'] > 10)
S = np.where(T['rpm9'] < 735, 'L', np.where((T['rpm9'] >= 780) & (T['rpm9'] <= 850), 'M', '-'))
# require IAC not on its floor for M on 11-26 (excess-air rpm)
S = np.where((S == 'M') & (T['iac_cmd'] <= 41), '-', S)
day = np.array([wday(x)[:5] for x in t])
pickle.dump(dict(tsr=tsr, S=S, ok=ok), open('rule.pkl', 'wb'))
if __name__ == '__main__':
    for dsel in ('11-25', 'all'):
        m = ok & (S != '-') & ((day == dsel) if dsel != 'all' else True)
        print(dsel, 'seconds', m.sum(), 'M share %.2f' % np.mean(S[m] == 'M'))
        for tau in (300, 360, 400, 420, 450, 480, 520, 560, 600, 700):
            p = tsr[m] >= tau; y = S[m] == 'M'
            print('  tau %3d  acc %.3f  M-recall %.3f  L-recall %.3f' % (tau, np.mean(p == y), np.mean(p[y]), np.mean(~p[~y])))
    # misclassified stretches at tau=420
    m = ok & (S != '-'); p = tsr >= 420
    bad = m & (p != (S == 'M'))
    for a, b in runs(bad):
        if b - a >= 5: print('miss d%d %s-%s %s tsr %.0f-%.0f rpm %.0f iac %.0f' % (T['drive'][a], wday(t[a]), wall(t[b]), S[a], tsr[a], tsr[b], np.nanmedian(T['rpm'][a:b+1]), np.nanmedian(T['iacv'][a:b+1])))
