from lib import *
import pickle, datetime as dt
H=pickle.load(open('H.pkl','rb'))
c=con(); print(c.execute("select st.name, count(*), hex(min(s.raw)) from samples s join streams st on st.drive_id=s.drive_id and st.id=s.stream_id where s.drive_id=1 group by st.name").fetchall())
d1=H[0x08][H[0x08][:,2]==1]; print('drive1 0x08 sample', [ [int(H[a][H[a][:,2]==1][i,1]) for a in range(8,16)] for i in (0,100,900)])
r=H[0x00].copy(); r[:,1]=(H[0x00][:,1]*256+H[0x01][:,1])/4
t=H[0x08][:,0]; drv=H[0x08][:,2]
rp,dr=at(r,t); el,de=at(H[0x19],t); ac,_=at(H[0x0B],t)
acb=(ac.astype(int)>>1)&1
run=(rp>550)&(dr<0.5)&(de<1.5)&(drv!=1)&(acb==0)
fan=run&(el<150); nofan=run&(el>=155)
print('n fan-on',fan.sum(),'fan-off',nofan.sum())
for a in range(0x08,0x10):
    v=H[a][:,1].astype(int)
    for b in range(8):
        x=(v>>b)&1; m=drv!=1
        if 0<x[m].mean()<1: print('0x%02X b%d  set %.4f, flips %d, fan-on %.3f, fan-off %.3f, rpm<550 %.3f'%(a,b,x[m].mean(),(np.diff(x[m])!=0).sum(),x[fan].mean(),x[nofan].mean(),x[(rp<550)&(dr<0.5)&m].mean()))
k=H[0x3C]; m=k[:,2]!=1; print('knock values excl d1',np.unique(k[m,1],return_counts=True))
rk,_=at(r,k[:,0]); el2,_=at(H[0x19],k[:,0]); ek,_=at(H[0x10],k[:,0])
for v in (0,1,2):
    s=m&(k[:,1]==v)
    if s.any(): print(' knock',v,'n',s.sum(),'rpm pct',np.percentile(rk[s],[5,50,95]),'ect med',np.median(ectc(ek[s])))
# knock transitions vs time
s=np.where(m)[0]; x=k[s,1]; ch=np.where(np.diff(x)!=0)[0]; print('knock changes',len(ch))
f=lambda u: dt.datetime.fromtimestamp(u).strftime('%m-%d %H:%M:%S')
for i in ch[:30]: print('  ',f(k[s[i+1],0]),int(x[i]),'->',int(x[i+1]),'rpm',rk[s[i+1]],'ect',round(float(ectc(ek[s[i+1]])),0))
