from lib import *
import pickle, datetime as dt
H=pickle.load(open('H.pkl','rb'))
f=lambda u: dt.datetime.fromtimestamp(u).strftime('%m-%d %H:%M:%S')
r=H[0x00].copy(); r[:,1]=(H[0x00][:,1]*256+H[0x01][:,1])/4
E=H[0x19]
# 1Hz grid per drive
out=[]
for d in sorted(set(E[:,2].astype(int))):
    s=E[:,2]==d; t0,t1=E[s,0][0],E[s,0][-1]
    tg=np.arange(t0,t1,1.0)
    g={k:at(a,tg) for k,a in {'eld':E,'alt':H[0x18],'bat':H[0x17],'rpm':r,'b0B':H[0x0B],'b08':H[0x08],'ect':H[0x10]}.items()}
    v={k:x[0] for k,x in g.items()}; fresh=(g['eld'][1]<1.5)&(g['rpm'][1]<1)
    ac=((v['b0B'].astype(int)>>1)&1)|((v['b08'].astype(int)>>1)&1)
    n=len(tg)
    for i in range(6,n-12):
        pre=slice(i-6,i-1); post=slice(i+2,i+10)
        if not (fresh[pre].all() and fresh[post].all()): continue
        if ac[i-6:i+10].any(): continue
        if np.ptp(v['rpm'][i-6:i+10])>120: continue
        de=np.median(v['eld'][post])-np.median(v['eld'][pre])
        if abs(de)>=12 and np.ptp(v['eld'][pre])<5 and np.ptp(v['eld'][post])<6:
            out.append((tg[i],d,de,np.median(v['eld'][pre]),np.median(v['alt'][post])-np.median(v['alt'][pre]),(np.median(v['bat'][post])-np.median(v['bat'][pre])),np.median(v['rpm'][post])-np.median(v['rpm'][pre]),ectc(np.median(v['ect'][pre])),ectc(np.median(v['ect'][i+10:i+60])) if i+60<n else np.nan, np.median(v['rpm'][pre])))
# dedupe
last=-99; ev=[]
for o in out:
    if o[0]-last>15: ev.append(o)
    last=o[0]
print('ELD steps (no A/C, steady rpm):',len(ev))
for o in ev: print('%s d%d dELD %+.0f (from %.0f)  dALTFR %+.1f  dBATT raw %+.1f  drpm %+.0f  ECT %.0f -> +1min %.0f  rpm %.0f'%(f(o[0]),*o[1:]))
A=np.array([o[2:7] for o in ev])
up=A[:,0]<0; dn=A[:,0]>0
for nm,m in (('ELD falls (load on)',up),('ELD rises (load off)',dn)):
    print(nm,'n',m.sum(),'median dELD %.0f dALTFR %.1f dBATT %.2f raw drpm %.0f'%tuple(np.median(A[m][:,[0,2,3,4]],axis=0)))
