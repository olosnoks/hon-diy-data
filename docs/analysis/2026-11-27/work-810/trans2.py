import numpy as np, pickle
from common import wall, wday, runs, idle
T = pickle.load(open('T2.pkl', 'rb')); C = pickle.load(open('ctx.pkl', 'rb'))
t = T['t']; N = len(t); er = T['b10']
ok = idle & (er <= 44) & (T['ect_max60'] <= 46) & (T['t_since_start'] > 30)
S = np.full(N, '-', dtype='<U1')
S[ok & (T['rpm9'] < 735)] = 'L'
S[ok & (T['rpm9'] >= 780) & (T['rpm9'] <= 850)] = 'M'
T['S'] = S
# stretches of >=5 labeled seconds with no other label in between, same run
st = []; cur = None
for i in range(N):
    if S[i] in 'LM':
        if cur and cur[0] == S[i] and T['run'][i] == T['run'][cur[2]]: cur[2] = i; cur[3] += 1
        else:
            if cur: st.append(cur)
            cur = [S[i], i, i, 1]
st.append(cur)
st = [s for s in st if s[3] >= 5]
m = []
for s in st:
    if m and m[-1][0] == s[0] and T['run'][m[-1][2]] == T['run'][s[1]]: m[-1][2] = s[2]; m[-1][3] += s[3]
    else: m.append(list(s))
st = m
def gapdesc(i0, i1):
    a, b = t[i0], t[i1]; d = []
    fans = [f for f in C['fans'] if a - 25 <= f[0] <= b]
    if fans: d.append('fan@%s' % ','.join(wall(f[0]) for f in fans))
    rv = [r for r in C['revs'] if a - 20 <= r[1] and r[0] <= b]
    if rv: d.append('rev%.0f@%s' % (max(r[2] for r in rv), wall(rv[0][0])))
    if [x for x in C['mov'] if x[1] >= a - 20 and x[0] <= b]: d.append('MOVE')
    mxr = np.nanmax(er[max(0, i0 - 20):i1 + 1])
    if mxr > 46: d.append('ECTmax%d' % mxr)
    if T['drive'][i0] != T['drive'][i1]: d.append('log-gap')
    if np.nansum(np.nan_to_num(T['b0b'][max(0, i0 - 20):i1 + 1]).astype(int) & 2): d.append('A/C')
    return ' '.join(d) if d else 'nothing'
EV = []
for k in range(1, len(st)):
    A, B = st[k - 1], st[k]
    if T['run'][A[2]] != T['run'][B[1]] or A[0] == B[0]: continue
    EV.append(dict(kind='entry' if B[0] == 'M' else 'exit', iA=A[2], iB=B[1], tA=t[A[2]], tB=t[B[1]], desc=gapdesc(A[2], B[1]), lenA=t[A[2]] - t[A[1]], lenB=t[B[2]] - t[B[1]]))
pickle.dump((st, EV, T), open('events.pkl', 'wb'))
if __name__ == '__main__':
    for e in EV:
        print('%-5s %s -> %s gap %4.0fs  before %5.0fs after %5.0fs  %s' % (e['kind'], wday(e['tA']), wall(e['tB']), e['tB'] - e['tA'], e['lenA'], e['lenB'], e['desc']))
    print(len(EV))
