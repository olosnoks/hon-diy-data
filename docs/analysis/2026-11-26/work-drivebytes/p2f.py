import pickle, numpy as np, pandas as pd
pd.set_option('display.width',250)
S,F=pickle.load(open('SF2.pkl','rb'))
print(S[S.b2f>=150][['drive','wall','b2f','rpm','vss','tps_raw','map','inj','b20','b10']].assign(wall=lambda d:d.wall.dt.strftime('%H:%M:%S')).to_string())
S['dtps']=S.groupby('drive').tps_raw.diff()
S['d2f']=S.groupby('drive').b2f.diff()
S['dt']=S.groupby('drive').t.diff()
# reset events: prev>0, now 0
S['prev2f']=S.groupby('drive').b2f.shift()
r=S[(S.b2f==0)&(S.prev2f>0)]
print('resets',len(r))
def why(x):
    if x.inj==0: return 'fuel cut'
    if abs(x.dtps)>=1: return 'tps change %+d'%x.dtps
    return 'other'
print(r.apply(why,axis=1).str.split().str[:2].str.join(' ').value_counts().head(20))
o=r[r.apply(why,axis=1)=='other']
print(o[['drive','wall','prev2f','rpm','vss','tps_raw','dtps','map','inj','b10','b0f','b08']].assign(wall=lambda d:d.wall.dt.strftime('%H:%M:%S')).to_string())
# rate model while counting (driving, both reads >0 and no reset)
m=(S.b2f>0)&(S.prev2f>0)&(S.vss>0)&(S.dt<1.5)
x=S[m]
x=x.assign(rate=x.d2f/x.dt, mrb=(x.b12//8*8), rb=(x.rpm//500*500))
print(x.pivot_table(index='mrb',columns='rb',values='rate',aggfunc='median').round(1).to_string())
# stft vs 0x2F at steady cruise
c=S[(S.vss>30)&(S.b2f>0)&(S.b0f==1)]
print('stft vs 0x2F corr', c[['b20','b2f']].corr(method='spearman').iloc[0,1].round(2), len(c))
for lo,hi in [(1,25),(25,40),(40,60),(60,256)]:
    cc=c[(c.b2f>=lo)&(c.b2f<hi)]; print(lo,hi,len(cc),'stft raw median',cc.b20.median(), 'map',cc['map'].median().round(1))
z=S[(S.vss>30)&(S.b0f==1)&(S.b2f==0)]; print('0x2F=0 cruise stft',z.b20.median(),len(z))
