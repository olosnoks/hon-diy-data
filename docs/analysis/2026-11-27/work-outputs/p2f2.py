from lib import *
import numpy as np
S = pd.read_pickle('P2F.pkl').sort_values('t').reset_index(drop=True)
R = S[(S.rpm > 400) & (S.vss > 5) & (S.cl == 1)]
R = R.assign(eb=pd.cut(R.ect_raw, [0, 50, 53, 55, 56, 57, 58, 60, 80]))
print(R.groupby('eb', observed=True).agg(n=('x2f','size'), frac_pos=('x2f', lambda x: (x > 0).mean()), ectC=('ect','median')).round(2))
# resets while staying in closed loop with fuel on (inj>0) before and after
S['px'] = S.groupby('did').x2f.shift(); S['pcl'] = S.groupby('did').cl.shift(); S['pstft'] = S.groupby('did').w_stft.shift()
S['ntps'] = S.groupby('did').w_tps.shift(-1)
rs = S[(S.px >= 40) & (S.x2f == 0) & (S.cl == 1) & (S.pcl == 1) & (S.inj > 0.5)]
print('\nresets from >=40 while closed loop stays on:', len(rs))
out = []
for i in rs.index:
    g = S.loc[i-3:i+6]
    out.append((hms(S.loc[i,'t']), int(S.loc[i,'px']), [round(v,1) for v in g.w_stft], [int(v) if pd.notna(v) else -1 for v in g.x2f], [round(v) for v in g.w_kpa], [round(v,0) for v in g.w_tps]))
for o in out: print(o[0], 'x2f before', o[1], '\n   stft', o[2], '\n   x2f ', o[3], '\n   kpa ', o[4], '\n   tps', o[5])
# mean stft trajectory relative to reset (closed loop resets)
traj = []
for i in rs.index:
    g = S.loc[i-3:i+8]
    if len(g) == 12 and g.cl.eq(1).all(): traj.append(g.w_stft.to_numpy())
traj = np.array(traj)
print('\nmean stft around resets (k=-3..+8), n=%d:' % len(traj), np.round(np.nanmean(traj, 0), 1))
