from lib import *
S = slow_all()
print(len(S), 'slow reads; drives', S.did.nunique())
for col, nm in [('b0b',None),('b0c',None),('b0d',None),('b0e',None),('b16',None),('b2a',None),('b2f',None)]:
    if col in S: 
        v = S[col].dropna(); print(col, 'n', len(v), 'unique', sorted(v.unique())[:20], 'nunique', v.nunique())
for b in range(8):
    for col in ['b0b','b0c']:
        v = bit(S[col], b).dropna()
        print(col, 'bit', b, 'set %.3f%%' % (100*v.mean()), 'n_set', int(v.sum()))
