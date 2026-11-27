"""Context series: engine runs, fan cycles (ELD steps), revs, ECT glitches; per-second idle state L/M."""
import numpy as np, pickle, datetime
from common import *
t = T['t']; did = T['drive']
def ts(s): return datetime.datetime.strptime(s, '%Y-%m-%d %H:%M:%S').timestamp()
STARTS = [ts(x) for x in ['2026-11-25 18:17:00', '2026-11-25 18:58:14', '2026-11-25 19:32:23', '2026-11-25 20:17:50',
                          '2026-11-25 21:19:55', '2026-11-26 20:32:12', '2026-11-26 22:51:09']]
st_idx = np.searchsorted(STARTS, t, side='right') - 1
T['run'] = st_idx; T['t_since_start'] = t - np.array(STARTS)[st_idx]
# fan cycles
fans = []
for d in np.unique(did):
    idx = np.nonzero(did == d)[0]; tt = t[idx]; el = T['eld'][idx]
    elf = np.array([np.nanmedian(el[max(0, i - 2):i + 3]) for i in range(len(el))])
    i = 10
    while i < len(tt) - 20:
        base = np.nanmedian(elf[i - 10:i - 2])
        if 4.5 < elf[i] - base < 11 and np.all(elf[i:i + 12] - base > 3.5) and np.all(np.diff(tt[i - 10:i + 12]) < 3):
            j = i
            while j < len(tt) - 1 and elf[j] - base > 3: j += 1
            k = i
            while k > i - 3 and el[k - 1] - base > 3.5: k -= 1
            fans.append((tt[k], tt[j], elf[i] - base, d)); i = j + 5
        else: i += 1
fans = np.array(fans)
def since(evt_t, tt, runs_ok=True):
    """seconds since last event time (inf if none in same engine run)"""
    out = np.full(len(tt), np.inf)
    ev = np.sort(np.asarray(evt_t))
    j = np.searchsorted(ev, tt, side='right') - 1
    ok = j >= 0
    out[ok] = tt[ok] - ev[j[ok]]
    evrun = np.searchsorted(STARTS, ev, side='right') - 1
    out[ok & (evrun[np.clip(j, 0, None)] != st_idx)] = np.inf
    return out
T['t_since_fan_on'] = since(fans[:, 0], t); T['t_since_fan_off'] = since(fans[:, 1], t)
T['fan_on'] = np.zeros(N, bool)
for a, b, _, _ in fans: T['fan_on'] |= (t >= a) & (t <= b + 2)
# revs from fast rpm, vss 0
revs = []
for d, F in FAST.items():
    m = (F[:, 1] > 1300) & (F[:, 2] < 1)
    for a, b in runs(m):
        if F[a, 0] - STARTS[np.searchsorted(STARTS, F[a, 0]) - 1] > 15:   # not the after-start flare
            revs.append((F[a, 0], F[b, 0], F[a:b + 1, 1].max()))
revs = np.array(revs)
T['t_since_rev'] = since(revs[:, 1], t)
# moving
mov = []
for d, F in FAST.items():
    for a, b in runs(F[:, 2] >= 3): mov.append((F[a, 0], F[b, 0]))
mov = np.array(mov) if mov else np.zeros((0, 2))
T['t_since_move'] = since(mov[:, 1], t) if len(mov) else np.full(N, np.inf)
# ECT: believable warm = raw <= 41 now and over last 60 s max raw <= 44 (no glitch)
er = T['b10']
mx60 = np.array([np.nanmax(er[max(0, i - 60):i + 1][did[max(0, i - 60):i + 1] == did[i]]) for i in range(N)])
T['ect_max60'] = mx60
T['ect_ok'] = (er <= 41) & (mx60 <= 44)
# glitch events: raw jump >= 15 between consecutive reads
gl = np.nonzero((np.abs(np.diff(er)) >= 15) & (np.diff(t) < 4))[0]
T['t_since_ect_glitch'] = since(t[gl + 1], t)
# idle state
r9 = np.array([np.nanmedian(T['rpm'][max(0, i - 4):i + 5]) for i in range(N)])
T['rpm9'] = r9
pickle.dump(dict(fans=fans, revs=revs, mov=mov, STARTS=STARTS, glitch=t[gl + 1]), open('ctx.pkl', 'wb'))
pickle.dump(T, open('T2.pkl', 'wb'))
print('fans', len(fans), 'revs', len(revs), 'moves', len(mov), 'glitches', len(gl))
