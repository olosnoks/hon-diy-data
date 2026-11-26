import pandas as pd, numpy as np
b=pd.read_pickle('bytes.pkl')
b=b.sort_values(['unix'])
# base grid: 12+4 reads (map etc) per drive
base=b[(b.addr==0x12)][['d','t','unix']].drop_duplicates().sort_values('unix')
out=base.copy()
for a in sorted(b.addr.unique()):
    s=b[b.addr==a][['unix','v','d']].rename(columns={'v':f'a{a:02X}'}).sort_values('unix')
    out=pd.merge_asof(out.sort_values('unix'),s,on='unix',by='d',direction='backward',tolerance=3.0)
out['rpm']=(out.a00*256+out.a01)/4
P=[155.04149,-3.0414878,0.03952185,-0.00029383913,0.0000010792568,-0.0000000015618437]
poly=lambda x: sum(c*x**i for i,c in enumerate(P))
out['ect']=poly(out.a10); out['iat']=poly(out.a11)
out.to_pickle('wide.pkl'); print(out.shape); print(out.describe().T.round(2).to_string())
