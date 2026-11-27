from lib import *
import numpy as np
S = slow_all([33,34,35,36,37,38,39]).sort_values('t').reset_index(drop=True)
S = S[S.x2a.notna()].reset_index(drop=True)
for did, g in S.groupby('did'):
    print(f'drive {did} {L[did]["name"]}: first x2a {g.x2a.iloc[0]:.0f} at {hms(g.t.iloc[0])}, last {g.x2a.iloc[-1]:.0f} at {hms(g.t.iloc[-1])}, n {len(g)}, moving reads {int((g.vss>0).sum())}')
ch = S.index[(S.x2a.diff() != 0) & (S.did == S.did.shift())]
print('\ntransitions:', len(ch))
for i in ch:
    w = S[(S.t > S.t[i] - 60) & (S.t <= S.t[i]) & (S.did == S.did[i])]
    print(hms(S.t[i]), f'd{S.did[i]}', f'{S.x2a[i-1]:.0f}->{S.x2a[i]:.0f}', f'ectraw {S.ect_raw[i]:.0f} ({S.ect[i]:.0f}C)', f'iac_cmd {S.iac_cmd[i]:.0f} (60s mean {w.iac_cmd.mean():.0f})', f'iacv {S.iacv[i]:.0f}', f'rpm {S.rpm[i]:.0f}', f'vss {S.vss[i]:.0f}', f'tgt {S.tgt[i]:.0f}')
# warm idle: equilibrium relation, reads with ECT raw<=38 and x2a stable for 60 s
W = S[(S.ect_raw <= 38) & (S.vss == 0)]
print('\nwarm (raw<=38) idle reads', len(W))
if len(W):
    W = W.assign(iacm=W.iac_cmd.rolling(40, min_periods=10).mean())
    print(W.groupby('x2a').agg(n=('iac_cmd','size'), iac_cmd=('iac_cmd','median'), iacv=('iacv','median'), rpm=('rpm','median')).round(1))
# is x2a related to iac_cmd when NOT warm?
N = S[(S.ect_raw > 38) & (S.vss == 0) & (S.rpm < 1000)]
print('\nnot-fully-warm idle by x2a'); print(N.groupby('x2a').agg(n=('iac_cmd','size'), iac_cmd=('iac_cmd','median'), iacv=('iacv','median'), ect_raw=('ect_raw','median'), rpm=('rpm','median')).round(1))
