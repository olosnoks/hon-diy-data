from lib import *
S = slow_all().sort_values(['did','t']).reset_index(drop=True)
S = S[S.rpm.notna()]
z = S.index[S.c0 == 0]
cols = ['rpm','kpa','vss','eld','b19','altfr','batt','iacv','iac_cmd','ect','ac','brake','b0c','b0b','b08']
for i in z:
    print('--- c0=0 at', hms(S.loc[i,'t']), 'drive', S.loc[i,'did'])
    for j in range(i-2, i+3):
        r = S.loc[j]; print('  ', hms(r.t), 'c0=%d' % r.c0, ' '.join(f'{c}={r[c]:.1f}' for c in cols))
# all ELD up-steps (current increase) >= 10 A between consecutive reads (dt<1.6), engine running
S['dt'] = S.groupby('did').t.diff(); S['deld'] = S.groupby('did').eld.diff()
S['next_c0'] = S.groupby('did').c0.shift(-1); S['prev_c0'] = S.groupby('did').c0.shift(1)
st = S[(S.dt < 1.6) & (S.deld >= 10) & (S.rpm > 400)]
print('\nELD steps >= +10 A:', len(st), ' with c0=0 in that read:', int((st.c0 == 0).sum()), ' in next read:', int((st.next_c0 == 0).sum()))
print(st[['t','did','eld','deld','batt','altfr','rpm','c0','next_c0']].assign(t=lambda d: d.t.map(hms)).to_string())
# batt minimum reads where c0 stayed 1
lo = S[(S.batt < 13.2) & (S.rpm > 400)]
print('\nreads with batt < 13.2 V and engine running:', len(lo), ' c0=0 among them:', int((lo.c0 == 0).sum()))
print(lo[['t','did','batt','eld','deld','altfr','rpm','c0']].assign(t=lambda d: d.t.map(hms)).to_string())
