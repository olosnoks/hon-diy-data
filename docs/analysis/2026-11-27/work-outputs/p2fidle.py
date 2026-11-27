from lib import *
import numpy as np
S = slow_all([33,34,35,36,37,38]).sort_values('t')
I = S[(S.vss == 0) & (S.rpm.between(600, 950)) & (S.cl == 1) & S.x2f.notna()]
I = I.assign(xb=pd.cut(I.x2f, [-1, 0, 10, 20, 30]), eb=pd.cut(I.ect_raw, [0, 45, 53, 58]))
print(I.groupby(['eb','xb'], observed=True).agg(n=('stft','size'), stft=('stft','mean'), ltft=('ltft','mean'), rpm=('rpm','median'), iac=('iac_cmd','median'), o2=('o2','mean')).round(2))
# episode starts at idle: stft mean 60 s before first nonzero vs 60-180 s after, and at plateau (x2f>=22)
S = S.reset_index(drop=True)
st = S.index[(S.x2f > 0) & (S.x2f.shift() == 0) & (S.vss == 0)]
rows = []
for i in st:
    t0 = S.t[i]
    b = S[(S.t > t0 - 90) & (S.t < t0) & (S.vss == 0) & (S.cl == 1)]
    a = S[(S.t > t0 + 240) & (S.t < t0 + 600) & (S.vss == 0) & (S.cl == 1) & (S.x2f >= 20)]
    if len(b) > 30 and len(a) > 30:
        rows.append((hms(t0), round(b.stft.mean(),1), round(a.stft.mean(),1), round(b.ltft.mean(),1), round(a.ltft.mean(),1), round(b.rpm.mean()), round(a.rpm.mean()), round(b.ect_raw.mean(),1), round(a.ect_raw.mean(),1)))
print('\nidle episodes: start, stft before, stft at plateau, ltft b/a, rpm b/a, ect raw b/a'); [print(r) for r in rows]
