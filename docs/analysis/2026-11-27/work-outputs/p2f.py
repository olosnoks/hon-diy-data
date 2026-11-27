from lib import *
import numpy as np
res = []
for did in [38, 39]:
    S = L[did]['slow']; S = decode(S.copy()).sort_values('t').reset_index(drop=True)
    F = fast(did)
    F['rich'] = (F.o2 > 0.45).astype(float)
    # window means of fast stft / o2 / rich / rpm / map / tps over +-0.5 s around each slow read
    ft = F.t.to_numpy()
    for c in ['stft','o2','rich','rpm','kpa','tps','inj']:
        v = F[c].to_numpy(); cs = np.r_[0, np.nancumsum(v)]; cn = np.r_[0, np.cumsum(~np.isnan(v))]
        lo = np.searchsorted(ft, S.t - 0.5); hi = np.searchsorted(ft, S.t + 0.5)
        S['w_' + c] = (cs[hi] - cs[lo]) / np.maximum(cn[hi] - cn[lo], 1)
        S.loc[(cn[hi] - cn[lo]) == 0, 'w_' + c] = np.nan
    # tps range in the 3 s before (steadiness)
    S['tps_rng'] = [np.ptp(F.tps.to_numpy()[np.searchsorted(ft, t-3):np.searchsorted(ft, t)+1]) if np.searchsorted(ft, t)-np.searchsorted(ft, t-3) > 3 else np.nan for t in S.t]
    # time since x2f was last 0 (reset)
    last0 = None; tsr = []
    for t, x in zip(S.t, S.x2f):
        if x == 0 or last0 is None: last0 = t
        tsr.append(t - last0)
    S['tsr'] = tsr; S['did'] = did
    res.append(S)
S = pd.concat(res, ignore_index=True)
S.to_pickle('P2F.pkl')
M = S[(S.vss > 5) & (S.cl == 1) & (S.fs == 2) & (S.w_stft.notna()) & (S.x2f.notna()) & (S.tps_rng < 3)].copy()
print('moving, closed loop, steady reads:', len(M))
M['xb'] = pd.cut(M.x2f, [-1, 0, 20, 60, 120, 200, 256], labels=['0','1-20','21-60','61-120','121-200','201-255'])
print(M.groupby('xb', observed=True).agg(n=('x2f','size'), stft=('w_stft','median'), ltft=('ltft','median'), rich=('w_rich','mean'), o2=('w_o2','median'), rpm=('w_rpm','median'), kpa=('w_kpa','median'), o2_2=('o2_2','median'), tsr=('tsr','median')).round(2).to_string())
# within-cell regression
M['cell'] = (M.w_rpm // 250).astype(int).astype(str) + '_' + (M.w_kpa // 5).astype(int).astype(str)
def within(y, xs, d):
    d = d.dropna(subset=[y] + xs)
    Z = d[[y] + xs] - d.groupby('cell')[[y] + xs].transform('mean')
    X = Z[xs].to_numpy(); Y = Z[y].to_numpy()
    b, *_ = np.linalg.lstsq(X, Y, rcond=None); r = Y - X @ b
    dof = len(d) - len(xs) - d.cell.nunique()
    se = np.sqrt((r @ r) / dof * np.linalg.inv(X.T @ X).diagonal())
    return b, se, len(d), d.cell.nunique()
for y in ['w_stft', 'w_rich', 'w_o2', 'o2_2']:
    b, se, n, nc = within(y, ['x2f'], M); print(f'{y}: within-cell slope per 100 counts of 0x2F = {100*b[0]:+.3f} +- {100*se[0]:.3f}  (n {n}, cells {nc})')
b, se, n, nc = within('w_stft', ['x2f', 'tsr'], M); print('stft ~ x2f + time-since-reset: per 100 counts %+.2f +- %.2f ; per 10 s %+.2f +- %.2f' % (100*b[0], 100*se[0], 10*b[1], 10*se[1]))
print('Spearman stft vs x2f', M[['w_stft','x2f']].corr('spearman').iloc[0,1].round(3), ' tsr vs x2f', M[['tsr','x2f']].corr('spearman').iloc[0,1].round(3))
# ECT gate while running
R = S[(S.rpm > 400)]
print('\nECT raw when x2f>0 (all running reads, drives 38-39): min/median/max', R[R.x2f>0].ect_raw.agg(['min','median','max']).tolist(), ' ECT degC max', R[R.x2f>0].ect.min().round(1))
print('ECT raw when x2f==0 :', R[R.x2f==0].ect_raw.describe().round(1).to_dict())
