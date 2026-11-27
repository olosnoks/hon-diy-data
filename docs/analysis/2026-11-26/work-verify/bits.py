from lib import *
import pickle, datetime as dt
H=pickle.load(open('H.pkl','rb'))
f=lambda u: dt.datetime.fromtimestamp(u).strftime('%m-%d %H:%M:%S')
r=H[0x00].copy(); r[:,1]=(H[0x00][:,1]*256+H[0x01][:,1])/4
t=H[0x08][:,0]
rp,dr=at(r,t); el,de=at(H[0x19],t); vs,_=at(H[0x02],t)
run=(rp>550)&(dr<0.5)&(de<1.5)
fan=run&(el<150); nofan=run&(el>=155)
print('n fan-on',fan.sum(),'fan-off',nofan.sum())
for a in range(0x08,0x10):
    v=H[a][:,1].astype(int)
    for b in range(8):
        x=(v>>b)&1
        p1=x[fan].mean(); p0=x[nofan].mean(); pall=x.mean()
        flips=(np.diff(x)!=0).sum()
        if 0<pall<1: print('0x%02X b%d  set %.3f overall, flips %d, fan-on %.3f, fan-off %.3f, key-off/crank(rpm<550) %.3f'%(a,b,pall,flips,p1,p0,x[(rp<550)&(dr<0.5)].mean()))
        else: print('0x%02X b%d  constant %d'%(a,b,x[0]))
# key-on and cranking battery
B=H[0x17]; rb,drb=at(r,B[:,0])
ko=(rb==0)&(drb<0.5); cr=(rb>0)&(rb<400)&(drb<0.5)
print('batt key-on raw',np.unique(B[ko,1],return_counts=True),'-> V',np.unique(B[ko,1])/10.45)
print('batt cranking raw',np.unique(B[cr,1],return_counts=True))
for d in sorted(set(B[ko|cr,2].astype(int))):
    s=(B[:,2]==d)
    print(' drive',d,'first 30 batt raw', B[s,1][:30].astype(int).tolist())
a=H[0x18]; ra,dra=at(r,a[:,0])
print('altfr key-on',np.unique(a[(ra==0)&(dra<0.5),1],return_counts=True))
k=H[0x3C]; print('knock values',np.unique(k[:,1],return_counts=True))
rk,_=at(r,k[:,0])
for v in (0,1,2): print(' knock',v,'rpm pct',np.percentile(rk[k[:,1]==v],[5,50,95]) if (k[:,1]==v).any() else '-')
