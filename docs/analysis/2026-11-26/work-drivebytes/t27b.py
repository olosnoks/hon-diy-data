import pickle, numpy as np, pandas as pd
pd.set_option('display.width',250); pd.set_option('display.max_columns',40); pd.set_option('display.max_rows',400)
S,F=pickle.load(open('SF2.pkl','rb'))
S=S[S.rpm>400].copy()
S['dmap']=S.groupby('drive')['map'].diff().abs()
S['dtps']=S.groupby('drive')['tps_raw'].diff().abs()
st=S[(S.dmap<3)&(S.dtps<3)&(S.inj>0)]
print('steady reads',len(st))
st=st.assign(mraw=st.b12, rb=(st.rpm//500*500).astype(int))
st['mb']=(st.b12//6*6).astype(int)
print(st.pivot_table(index='mb',columns='rb',values='b27',aggfunc='median').to_string())
print(st.pivot_table(index='mb',columns='rb',values='b27',aggfunc='count').fillna(0).astype(int).to_string())
# 0x3C vs rpm
print('0x3C: rpm quantiles per value (all reads, rpm from fast block nearest)')
for v,g in S.groupby('b3c'):
    print(int(v), len(g), g.rpm.quantile([0,.05,.5,.95,1]).round(0).values)
