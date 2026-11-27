import pickle, numpy as np, pandas as pd, sqlite3
pd.set_option('display.width',250); pd.set_option('display.max_rows',500)
S,F=pickle.load(open('SF2.pkl','rb'))
c=sqlite3.connect('file:hondalog.db?mode=ro',uri=True)
G={}
for name,did in [('2224',38),('2251',39)]:
    g=pd.DataFrame(c.execute("select g.t,g.v2,g.v3 from samples g join streams st on st.drive_id=g.drive_id and st.id=g.stream_id where g.drive_id=? and st.name='gps' and g.part=0 order by g.t",(did,)).fetchall(),columns=['t','spd','hdg'])
    d=g.hdg.diff(); d=(d+180)%360-180
    g['yaw']=d/g.t.diff()
    G[name]=g
out=[]
for i,r in S.iterrows():
    g=G[r.drive]; j=(g.t-r.t).abs().idxmin()
    w=g[(g.t>r.t-3)&(g.t<r.t+3)]
    out.append(w.yaw.abs().max() if len(w) else np.nan)
S['yaw3']=out
S['psp']=(S.b08.astype(int)//4)%2
print('PSP=0 reads:')
print(S[S.psp==0][['drive','wall','psp','rpm','vss','tps_raw','b28','b29','yaw3','b08']].assign(wall=lambda d:d.wall.dt.strftime('%H:%M:%S')).to_string())
mv=S[(S.vss>0)&(S.vss<25)]
print('low speed: max |yaw| (deg/s, +-3 s) psp=0 median',S[S.psp==0].yaw3.median(),' psp=1 median', mv[mv.psp==1].yaw3.median())
print('share psp=0 by |yaw| band at vss 1-25:')
print(mv.groupby(pd.cut(mv.yaw3,[0,5,10,20,40,200])).psp.agg(lambda s:(s==0).mean()).round(3), mv.groupby(pd.cut(mv.yaw3,[0,5,10,20,40,200])).size())
pickle.dump(S,open('S_yaw.pkl','wb'))
