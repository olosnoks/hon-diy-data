"""Stable L/M stretches and the transitions between them (same engine run)."""
import numpy as np, pickle
from common import wall, wday, runs, idle
T = pickle.load(open('T2.pkl', 'rb')); C = pickle.load(open('ctx.pkl', 'rb'))
t = T['t']; N = len(t); er = T['b10']
mx60 = T['ect_max60']
ok = idle & (er <= 44) & (mx60 <= 46) & ~T['fan_on'] & (T['t_since_start'] > 30)
S = np.full(N, '-', dtype='<U1')
S[ok & (T['rpm9'] < 735)] = 'L'
S[ok & (T['rpm9'] >= 780) & (T['rpm9'] <= 850)] = 'M'
T['S'] = S
# stretches: maximal runs of one letter, allowing '-' gaps; break on other letter or engine run change
st = []
cur = None
for i in range(N):
    if S[i] in 'LM':
        if cur and cur[0] == S[i] and T['run'][i] == T['run'][cur[2]]:
            cur[2] = i; cur[3] += 1
        else:
            if cur: st.append(cur)
            cur = [S[i], i, i, 1]
        # a different letter closes
if cur: st.append(cur)
st = [s for s in st if s[3] >= 8]
# merge adjacent same-letter after filtering
m = []
for s in st:
    if m and m[-1][0] == s[0] and T['run'][m[-1][2]] == T['run'][s[1]]:
        m[-1][2] = s[2]; m[-1][3] += s[3]
    else: m.append(list(s))
st = m
pickle.dump((st, T), open('stretches.pkl', 'wb'))
def gapdesc(i0, i1):
    """what happened between index i0 (last of stretch A) and i1 (first of stretch B)"""
    a, b = t[i0], t[i1]
    d = []
    fans = [f for f in C['fans'] if a - 5 <= f[0] <= b]
    if fans: d.append('fan@%s' % ','.join(wall(f[0]) for f in fans))
    rv = [r for r in C['revs'] if a - 2 <= r[0] <= b]
    if rv: d.append('rev%.0f@%s' % (max(r[2] for r in rv), wall(rv[0][0])))
    mv = [x for x in C['mov'] if x[1] >= a and x[0] <= b]
    if mv: d.append('MOVE')
    seg = slice(i0, i1 + 1)
    mxr = np.nanmax(er[seg])
    if mxr > 46: d.append('ECTmax%d' % mxr)
    if T['drive'][i0] != T['drive'][i1]: d.append('log-gap %.0fs' % (t[T['drive'] == T['drive'][i1]][0] - t[T['drive'] == T['drive'][i0]][-1]))
    ac = np.nansum(T['b0b'][seg].astype(int) & 2) if np.isfinite(T['b0b'][seg]).all() else 0
    if ac: d.append('A/C')
    return ' '.join(d) if d else 'nothing'
if __name__ == '__main__':
    for s in st:
        print('%s d%-2d %s - %s  %5.0fs (n%4d) rpm %4.0f iacv %4.1f' % (s[0], T['drive'][s[1]], wday(t[s[1]]), wall(t[s[2]]), t[s[2]] - t[s[1]], s[3],
              np.nanmedian(T['rpm'][s[1]:s[2] + 1][S[s[1]:s[2] + 1] == s[0]]), np.nanmedian(T['iacv'][s[1]:s[2] + 1][S[s[1]:s[2] + 1] == s[0]])))
    print()
    for k in range(1, len(st)):
        A, B = st[k - 1], st[k]
        if T['run'][A[2]] != T['run'][B[1]]: continue
        if A[0] == B[0]: continue
        kind = 'ENTRY' if B[0] == 'M' else 'EXIT '
        print('%s %s -> %s gap %4.0fs  run%d  %s' % (kind, wday(t[A[2]]), wall(t[B[1]]), t[B[1]] - t[A[2]], T['run'][A[2]], gapdesc(A[2], B[1])))
