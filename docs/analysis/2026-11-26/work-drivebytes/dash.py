import pickle, numpy as np, pandas as pd
pd.set_option('display.width',250); pd.set_option('display.max_rows',500)
S,F=pickle.load(open('SF2.pkl','rb'))
ct=S[(S.tps_raw<=21)]
ct=ct.assign(vb=pd.cut(ct.vss,[-1,0,5,15,30,60,120]), eb=pd.cut(ct.b10,[0,47,50,53,56,60,80]), cut=ct.inj==0)
print('closed throttle: idle_target median by vss band x ECT raw band')
print(ct.pivot_table(index='vb',columns='eb',values='idle_target',aggfunc='median',observed=True))
print('closed throttle: 0x28 median by vss band, fuel cut or not')
print(ct.pivot_table(index='vb',columns='cut',values='b28',aggfunc=['median','count'],observed=True))
print('stopped, closed throttle: 0x28 / idle target / rpm by ECT band')
st=ct[ct.vss==0]
print(st.groupby('eb',observed=True)[['b28','b29','idle_target','rpm']].median())
# time since stop: decay of 0x28 after vss reaches 0
S['stopped']=(S.vss==0)
res=[]
for d,g in S.groupby('drive'):
    g=g.reset_index(drop=True)
    starts=g.index[(g.stopped)&(~g.stopped.shift(fill_value=True))]
    for s in starts:
        w=g.iloc[s:s+30]
        w=w[w.stopped & (w.tps_raw<=21)]
        for _,r in w.iterrows(): res.append((r.t-g.loc[s,'t'], r.b28, r.rpm, r.idle_target))
R=pd.DataFrame(res,columns=['dt','b28','rpm','tgt'])
R['db']=(R.dt//3*3)
print('after the car stops (closed throttle): median by seconds since stop')
print(R.groupby('db')[['b28','rpm','tgt']].median().head(10), R.groupby('db').size().head(10).values)
