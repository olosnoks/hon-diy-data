import numpy as np, lib, datetime, pickle
from common import T, DAY, runs
w = lib.wall
def tt(d, s): return datetime.datetime.strptime('2026-11-%s %s' % (d, s), '%Y-%m-%d %H:%M:%S').timestamp()
t = T['t']; did = T['drive']
v0 = np.nan_to_num(T['vss']) < 0.5
base = v0 & (T['tps'] < 9) & (T['rpm'] > 550) & (T['rpm'] < 1300) & ~(np.nan_to_num(T['ac_clutch']) > 0)
# exclude 20 s after any ECT step >= 4 raw counts... use 4 deg C
e = T['ect']; step = np.zeros(len(t), bool)
de = np.abs(np.diff(e)); big = np.nonzero(de >= 4)[0] + 1
for i in big: step[i:i + 25] = True
# exclude 10 s around fan-on
fan = pickle.load(open('fan.pkl', 'rb'))
fanm = np.zeros(len(t), bool)
for o in fan:
    if o['dur'] < 30: fanm |= (t >= o['t'] - 3) & (t <= o['t'] + o['dur'] + 15)
steady = base & ~step & ~fanm
# rpm steady: |rpm - median 15 s| < 40
r = T['rpm']
G = {}
G['11-25 parked, ECT reads warm (>=77 C), L ~670'] = steady & (DAY == '11-25') & (T['ect_raw'] <= 44) & (r < 730)
G['11-25 parked, ECT reads warm (>=77 C), M ~810'] = steady & (DAY == '11-25') & (T['ect_raw'] <= 44) & (r > 780) & (r < 850)
G['11-26 parked, good contact (d35, ECT >=77 C)'] = steady & (did == 35) & (T['ect_raw'] <= 44)
G['11-26 parked, low reading 69-75 C (target 810-837)'] = steady & (DAY == '11-26') & (T['ect_raw'] >= 45) & (T['ect_raw'] <= 54) & ((did < 38) | (t < tt('26', '22:39:40')))
G['11-26 parked, low reading 63-68 C (target 850-880)'] = steady & (DAY == '11-26') & (T['ect_raw'] >= 55) & (T['ect_raw'] <= 61) & ((did < 38) | (t < tt('26', '22:39:40')))
G['after driving: gas station 22:43:30-22:45:24'] = steady & (t >= tt('26', '22:43:30')) & (t <= tt('26', '22:45:24'))
G['after driving: hot restart 22:51:30-22:52:50'] = steady & (t >= tt('26', '22:51:30')) & (t <= tt('26', '22:52:50'))
G['after driving: home 23:26:35-23:26:58'] = steady & (t >= tt('26', '23:26:35')) & (t <= tt('26', '23:26:58'))
G['after driving: all three'] = G['after driving: gas station 22:43:30-22:45:24'] | G['after driving: hot restart 22:51:30-22:52:50'] | G['after driving: home 23:26:35-23:26:58']
G['after driving, ECT 69-70 C only'] = G['after driving: all three'] & (T['ect_raw'] >= 51) & (T['ect_raw'] <= 54)
G['parked 11-26, ECT 69-70 C only (match)'] = steady & (DAY == '11-26') & (T['ect_raw'] >= 51) & (T['ect_raw'] <= 54) & ((did < 38) | (t < tt('26', '22:39:40')))
cols = ['rpm', 'idle_target', 'ect', 'iacv', 'iac_cmd', 'x2a', 'stft', 'ltft', 'map', 'inj', 'ign', 'eld', 'iat', 'batt']
rows = []
print('%-52s %5s ' % ('group', 'n s') + ' '.join('%7s' % c[:7] for c in cols) + '   total  rpm-tgt  o2>0.45')
for k, m in G.items():
    vals = [np.nanmedian(T[c][m]) if m.sum() else np.nan for c in cols]
    tot = np.nanmedian(T['stft'][m] + T['ltft'][m])
    dt = np.nanmedian(T['rpm'][m] - T['idle_target'][m]) if np.isfinite(T['idle_target'][m]).any() else np.nan
    o2 = np.nanmean(T['o2'][m] > 0.45)
    rows.append((k, m.sum(), vals, tot, dt))
    print('%-52s %5d ' % (k, m.sum()) + ' '.join('%7.1f' % x for x in vals) + '   %5.1f  %6.0f  %5.2f' % (tot, dt, o2))
pickle.dump((G, rows, cols), open('groups.pkl', 'wb'))
