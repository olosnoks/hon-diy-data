from lib import *
S = slow_all().sort_values(['did','t']).reset_index(drop=True)
# time since start: first read of the drive with starter or rpm>0
S['tss'] = S.t - S.groupby('did').t.transform('min')
cols = ['rpm','kpa','tps','load','vss','ect','iat','cl','fs','inj','stft','o2','o2_2','batt','altfr','eld','b0b','x2f']
print('b6 = 0 reads:', int((S.b6==0).sum()))
for did, g in S.groupby('did'):
    g = g.reset_index(drop=True)
    ch = g.index[g.b6.diff().fillna(0) != 0].tolist()
    z = g.index[g.b6 == 0].tolist()
    if not z: continue
    print(f'\n=== drive {did} {L[did]["name"]}: {len(z)} zero reads, {len(ch)} edges')
    show = sorted(set(sum([[i-2,i-1,i,i+1] for i in z], [])))
    for i in show:
        if 0 <= i < len(g):
            r = g.loc[i]
            print(hms(r.t), 'b6=%d' % r.b6, ' '.join(f'{c}={r[c]:.1f}' if pd.notna(r[c]) else f'{c}=nan' for c in cols), 'tss=%.0f' % r.tss)
