from lib import *
import pickle, datetime as dt
H=pickle.load(open('H.pkl','rb'))
f=lambda u: dt.datetime.fromtimestamp(u).strftime('%m-%d %H:%M:%S')
r=H[0x00].copy(); r[:,1]=(H[0x00][:,1]*256+H[0x01][:,1])/4
E=H[0x19]; t=E[:,0]
bt,_=at(H[0x17],t); af,_=at(H[0x18],t); rp,dr=at(r,t); ac,_=at(H[0x0B],t); ec,_=at(H[0x10],t); c08,_=at(H[0x08],t)
ok=dr<0.5
print('overall running (rpm>500):')
run=ok&(rp>500)
for nm,v in (('eld',E[:,1]),('batt',bt),('altfr',af)):
    print(' ',nm,np.percentile(v[run],[1,5,50,95,99]))
print('corr eld-altfr',np.corrcoef(E[run,1],af[run])[0,1],'eld-batt',np.corrcoef(E[run,1],bt[run])[0,1],'altfr-rpm',np.corrcoef(af[run],rp[run])[0,1],'eld-rpm',np.corrcoef(E[run,1],rp[run])[0,1])
# A/C clutch transitions
acb=(ac.astype(int)>>1)&1
idx=np.where(np.diff(acb)!=0)[0]
print('A/C clutch transitions',len(idx))
def win(i,pre=3,post=6):
    t0=t[i+1]; s=(t>=t0-pre)&(t<t0-0.5)&run; p=(t>=t0+1.5)&(t<t0+post)&run
    if s.sum()<2 or p.sum()<2: return None
    return [np.median(x[p])-np.median(x[s]) for x in (E[:,1],bt,af,rp)]+[np.median(E[s,1]),np.median(af[s])]
for i in idx:
    w=win(i)
    if w: print(' ',f(t[i+1]),'AC',int(acb[i]),'->',int(acb[i+1]),' dELD %+.0f dBATTraw %+.1f dALTFR %+.0f drpm %+.0f  (ELD before %.0f ALTFR before %.0f)'%tuple(w))
# ELD step detection (median filter 1Hz)
