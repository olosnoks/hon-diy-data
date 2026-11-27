import pickle, numpy as np, pandas as pd
pd.set_option('display.width',250); pd.set_option('display.max_rows',500)
S,F=pickle.load(open('SF2.pkl','rb'))
W=lambda d: d.assign(wall=d.wall.dt.strftime('%H:%M:%S'))
print('== start of 2251: first 20 slow reads')
print(W(S[S.drive=='2251'].head(16))[['wall','rpm','b0b','b0f','b9a','b3a','b32','b37','b27','b2a','b2f','ba0','b15','b10','b17']].to_string())
print('== 0x2A changes'); 
x=S[['drive','wall','b2a','b10','b28','rpm','vss']]; x=x[x.b2a!=x.groupby('drive').b2a.shift()]; print(W(x).to_string())
print('== 0x32 / 0x37 dip rate by rpm band')
S['rb']=pd.cut(S.rpm,[0,900,1500,2000,2500,3000,6000])
print(S.groupby('rb',observed=True).agg(n=('b32','size'),d32=('b32',lambda s:(s==44).mean()),d37=('b37',lambda s:(s==44).mean()),c3c=('b3c','median')).round(3))
print('== rear O2 0xA0 (x/51.3 V) by state')
S['state']=np.select([S.b9a==1,(S.inj==0)&(S.rpm>900),(S.b9a==4),(S.vss==0),(S.vss>0)],['after-start','fuel cut','open-loop power','idle','cruise/closed loop'],'other')
g=S.groupby('state').agg(n=('ba0','size'),a0_med=('ba0','median'),a0_p10=('ba0',lambda s:s.quantile(.1)),a0_p90=('ba0',lambda s:s.quantile(.9)),o2_med=('b15','median'))
print(g)
c=S[(S.vss>60)&(S.b0f==1)&(S.b9a==2)]
print('highway closed loop: 0xA0 V median %.3f, IQR %.3f-%.3f; front O2 switching share >0.45V %.2f; corr front/rear %.2f'%(c.ba0.median()/51.3,c.ba0.quantile(.25)/51.3,c.ba0.quantile(.75)/51.3,(c.b15/51.3>0.45).mean(),c[['ba0','b15']].corr().iloc[0,1]))
# lag: 0xA0 vs b15 at slow rate, lag in reads
for lag in range(-3,6):
    print(lag, round(c.ba0.corr(c.b15.shift(lag)),2), end='; ')
print()
# fuel cut: rear O2 goes to 0?
fc=S[(S.inj==0)&(S.rpm>900)]
print('fuel cut 0xA0 median',fc.ba0.median(),'front',fc.b15.median(),'n',len(fc))
